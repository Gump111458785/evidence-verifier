import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import ARCHIVE
from training import gate, encode, data_rows, train


class PreparationTests(unittest.TestCase):
    def test_formal_training_is_rejected_before_output_or_weights(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as d:
            report = Path(d)/'gate.json'
            report.write_text(json.dumps(ARCHIVE['models']['qwen14b']['evaluation']))
            config = json.loads((root/'configs/sft.example.json').read_text())
            output = Path(d)/'must-not-exist'
            with self.assertRaisesRegex(RuntimeError, 'development gate has not passed'):
                train(config, root/'configs/sft-fixture.jsonl', report, output)
            self.assertFalse(output.exists())

    def test_current_development_gate_is_not_promoted_to_training(self):
        report = copy.deepcopy(ARCHIVE['models']['qwen14b']['evaluation'])
        report['split']['regression80']['passed'] = True
        result = gate(report)
        self.assertFalse(result['passed'])
        self.assertEqual(result['counts']['joint'], 69)
        self.assertEqual(result['counts']['positive'], 32)

    def test_duplicate_diagnostic_rows_cannot_pass_gate(self):
        report = copy.deepcopy(ARCHIVE['models']['qwen14b']['evaluation'])
        rows = report['split']['regression80']['details']
        rows[1] = rows[0]
        with self.assertRaisesRegex(ValueError, 'unique'):
            gate(report)

    def test_extraction_training_cannot_contain_candidate(self):
        row = {'id': 'x', 'split': 'train', 'task': 'extract', 'source_group': 'fixture', 'label_source': 'synthetic_fixture',
               'messages': [{'role': 'system', 'content': 'extract'},
                            {'role': 'user', 'content': '{"evidence":[],"candidate":"answer"}'},
                            {'role': 'assistant', 'content': '{"facts":[]}'}]}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'data.jsonl';p.write_text(json.dumps(row))
            with self.assertRaisesRegex(ValueError, 'must not include'):
                data_rows(p)

    def test_response_mask_uses_whole_sequence_offsets(self):
        class Tokenizer:
            def apply_chat_template(self, messages, tokenize, add_generation_prompt):
                return 'PREFIX' if add_generation_prompt else 'PREFIXRESPONSE'
            def __call__(self, text, **kwargs):
                # The middle token crosses the prompt/response boundary: mask it.
                return {'input_ids': [1, 2, 3], 'offset_mapping': [(0, 4), (4, 8), (8, 14)]}
        row = {'id': 'fixture', 'messages': [{}, {}, {}]}
        self.assertEqual(encode(row, Tokenizer(), 10)['labels'], [-100, -100, 3])
        with self.assertRaisesRegex(ValueError, 'no silent truncation'):
            encode(row, Tokenizer(), 2)


@unittest.skipUnless(importlib.util.find_spec('torch') and importlib.util.find_spec('transformers') and importlib.util.find_spec('peft'), 'Requires existing PyTorch/PEFT environment')
class TorchTrainingTests(unittest.TestCase):
    def test_checkpointed_projection_does_not_retain_full_logits(self):
        import torch
        import torch.nn.functional as F
        from torch_training import chunked_ce
        h = torch.randn(2, 9, 13, requires_grad=True)
        w = torch.randn(29, 13, requires_grad=True)
        y = torch.randint(0, 29, (2, 9))
        def retained(fn):
            saved = []
            def pack(t):
                saved.append(tuple(t.shape));return t
            with torch.autograd.graph.saved_tensors_hooks(pack, lambda t: t):
                value = fn()
            value.backward()
            return saved
        standard = retained(lambda: F.cross_entropy(F.linear(h[:, :-1], w).reshape(-1, 29), y[:, 1:].reshape(-1)))
        h.grad = None;w.grad = None
        chunked = retained(lambda: chunked_ce(h, y, w, chunk_tokens=3))
        self.assertIn((16, 29), standard)
        self.assertNotIn((16, 29), chunked)

    def test_chunked_loss_and_gradients_match_standard_projection(self):
        import torch
        import torch.nn.functional as F
        from torch_training import chunked_ce
        torch.manual_seed(3)
        h = torch.randn(2, 7, 12, requires_grad=True)
        w = torch.randn(23, 12, requires_grad=True)
        y = torch.randint(0, 23, (2, 7));y[:, :3] = -100;y[1, -1] = -100
        loss = F.cross_entropy(F.linear(h[:, :-1], w).reshape(-1, 23), y[:, 1:].reshape(-1), ignore_index=-100)
        expected = torch.autograd.grad(loss, (h, w))
        for n in (1, 3, 99):
            result = chunked_ce(h, y, w, chunk_tokens=n)
            gradients = torch.autograd.grad(result, (h, w))
            torch.testing.assert_close(result, loss)
            for actual, reference in zip(gradients, expected):
                torch.testing.assert_close(actual, reference, atol=2e-6, rtol=2e-5)

    def test_tiny_lora_update_save_and_reload_on_cpu(self):
        import torch
        from transformers import LlamaConfig, LlamaForCausalLM
        from peft import LoraConfig, get_peft_model, PeftModel
        from torch_training import optimize
        torch.manual_seed(2)
        config = LlamaConfig(vocab_size=64, hidden_size=32, intermediate_size=64,
                             num_hidden_layers=1, num_attention_heads=4, num_key_value_heads=2,
                             max_position_embeddings=64, attention_dropout=0)
        model = get_peft_model(LlamaForCausalLM(config), LoraConfig(task_type='CAUSAL_LM', r=2, lora_alpha=4,
                              lora_dropout=0, target_modules=['q_proj', 'v_proj']))
        before = {k: v.detach().clone() for k, v in model.named_parameters()}
        items = [{'id': 'a', 'input_ids': [1, 5, 6, 7, 2], 'labels': [-100, -100, 6, 7, 2]},
                 {'id': 'b', 'input_ids': [1, 3, 4, 2], 'labels': [-100, 3, 4, 2]}]
        options = {'micro_batch': 1, 'gradient_accumulation': 2, 'optimizer_steps': 2,
                   'loss_mode': 'checkpointed_chunked_ce', 'chunk_tokens': 2, 'learning_rate': .01}
        with tempfile.TemporaryDirectory() as d:
            result = optimize(model, items, options, 0, Path(d)/'steps.jsonl')
            self.assertEqual(result['device'], 'cpu')
            self.assertEqual(result['optimizer_steps'], 2)
            self.assertIsNone(result['peak_allocated_bytes'])
            changed = [k for k, v in model.named_parameters() if not torch.equal(v.detach(), before[k])]
            self.assertTrue(changed)
            self.assertTrue(all('lora_' in k for k in changed), changed)
            model.save_pretrained(d)
            restored = PeftModel.from_pretrained(LlamaForCausalLM(config), d)
            for k, v in restored.named_parameters():
                if 'lora_' in k:
                    torch.testing.assert_close(v, dict(model.named_parameters())[k])


if __name__ == '__main__':
    unittest.main()
