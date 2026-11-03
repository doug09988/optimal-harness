import ast
import unittest

from optimal_harness.policy import PolicyError
from optimal_harness.qa import DrivenQA


def make_qa(name='test_stream_spacing'):
    qa = DrivenQA()
    qa.plan = [name]
    return qa


class TestQABodyExtraction(unittest.TestCase):
    def test_class_wrapped_body_extracted_and_imports_hoisted(self):
        qa = make_qa()
        repairs = []
        body = ("```python\nimport unittest\nfrom ev_publisher import stream\n\n"
                "class TestStream(unittest.TestCase):\n"
                "    def test_stream_spacing(self):\n"
                "        \"\"\"Spacing is i / hz.\"\"\"\n"
                "        self.assertEqual(list(stream([], hz=20)), [])\n```")
        qa.accept(body, repaired=repairs.append)
        self.assertEqual(repairs, [{'name': 'test_stream_spacing',
                                    'repair_kind': 'qa_body_extraction'}])
        code = qa.assembly()
        self.assertIn('def test_stream_spacing(self):', code)
        fn = ast.parse(code).body[-1].body[0]
        self.assertIsInstance(fn, ast.FunctionDef)
        self.assertTrue(any(isinstance(stmt, ast.ImportFrom) for stmt in fn.body))
        compile(code, '<qa-test>', 'exec')

    def test_top_level_function_among_imports_extracted(self):
        qa = make_qa('test_one')
        body = ("import os\n\n"
                "def test_one():\n    assert os is not None\n")
        qa.accept(body, repaired=lambda entry: None)
        self.assertIn('def test_one():', qa.assembly())
        self.assertIn('import os', qa.bodies['test_one'])

    def test_wrong_name_class_method_still_rejected(self):
        qa = make_qa('test_one')
        body = ("import unittest\n\nclass T(unittest.TestCase):\n"
                "    def test_other(self):\n        pass\n")
        with self.assertRaises(PolicyError):
            qa.accept(body)
        self.assertEqual(qa.bodies, {})

    def test_bare_function_unchanged_no_repair(self):
        qa = make_qa('test_one')
        repairs = []
        qa.accept('def test_one(self):\n    self.assertTrue(True)', repaired=repairs.append)
        self.assertEqual(repairs, [])
        self.assertIn('def test_one(self):', qa.assembly())


if __name__ == '__main__':
    unittest.main()
