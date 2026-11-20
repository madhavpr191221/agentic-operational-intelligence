import ast
import unittest
from pathlib import Path

import agentic_ops


class ArchitectureBoundaryTests(unittest.TestCase):
    def test_investigator_and_evidence_layers_do_not_import_detection(self):
        package_root = Path(agentic_ops.__file__).parent
        for relative_path in ("agent.py", "evidence.py"):
            tree = ast.parse((package_root / relative_path).read_text(encoding="utf-8"))
            imports = []
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    imports.append(node.module)
                elif isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
            self.assertFalse(any(name.startswith(("agentic_ops.detection", "agentic_ops.detectors"))
                                 for name in imports), relative_path)

    def test_detection_engine_depends_only_on_its_contract_inside_the_app(self):
        engine_path = Path(agentic_ops.__file__).parent / "detection" / "engine.py"
        tree = ast.parse(engine_path.read_text(encoding="utf-8"))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
            elif isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
        application_imports = [name for name in imports if name.startswith("agentic_ops.")]
        self.assertEqual(application_imports, ["agentic_ops.detection.contracts"])


if __name__ == "__main__":
    unittest.main()
