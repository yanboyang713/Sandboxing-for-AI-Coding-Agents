#!/usr/bin/env python3
import os, json, logging
from dataclasses import dataclass
from typing import Dict, Any
from sandbox import DockerSandbox, SandboxPolicy, Transaction
from openai import OpenAI

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

SYSTEM_PROMPT = """You are a senior Python engineer.
Write ONE self-contained Python module that defines quicksort(lst: list[int]) -> list[int]
- Use pure Python (no external deps).
- Include a __main__ guard that:
  * reads optional integers from stdin (space-separated),
  * prints the sorted list as space-separated ints.
- Add docstring and small doctest examples.
Return only the code (no backticks)."""

@dataclass
class AgentConfig:
    model: str = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    temperature: float = float(os.getenv("AGENT_TEMPERATURE", "0.2"))

class QuicksortAgent:
    def __init__(self, sandbox: DockerSandbox, cfg: AgentConfig):
        self.sandbox = sandbox
        self.cfg = cfg
        self.oa = OpenAI()  # uses OPENAI_API_KEY

    def generate_code(self) -> str:
        kwargs = {
            "model": self.cfg.model,
            "input": [{"role": "system", "content": SYSTEM_PROMPT}],
        }
        # Only send temperature if not explicitly disabled
        if not os.getenv("AGENT_DISABLE_TEMPERATURE"):
            kwargs["temperature"] = self.cfg.temperature

        try:
            r = self.oa.responses.create(**kwargs)
        except Exception as e:
            # Some models (e.g., gpt-5-nano) reject 'temperature'
            if "Unsupported parameter: 'temperature'" in str(e):
                kwargs.pop("temperature", None)
                r = self.oa.responses.create(**kwargs)
            else:
                raise

        code = getattr(r, "output_text", None)
        if not code:
            # Fallback extraction
            out = []
            for item in getattr(r, "output", []):
                if getattr(item, "type", "") == "message":
                    for c in getattr(item, "content", []):
                        if getattr(c, "type", "") == "output_text":
                            out.append(c.text)
            code = "".join(out).strip()
        if "def quicksort(" not in code:
            raise RuntimeError("Model did not return expected quicksort code.")
        return code

    def test_in_sandbox(self, code: str) -> Dict[str, Any]:
        ws = self.sandbox.workspace
        os.makedirs(ws, exist_ok=True)
        with open(os.path.join(ws, "quicksort.py"), "w") as f:
            f.write(code)

        test = (
            'python - << "PY"\n'
            'from quicksort import quicksort\n'
            'assert quicksort([3,1,2]) == [1,2,3]\n'
            'assert quicksort([]) == []\n'
            'assert quicksort([5,4,4,1]) == [1,4,4,5]\n'
            'print("OK")\n'
            'PY'
        )
        return self.sandbox.run_sequence([test], timeout=15)

    def run_demo(self) -> None:
        for attempt in range(1, 4):
            with Transaction(self.sandbox):
                code = self.generate_code()
                res = self.test_in_sandbox(code)
                if res["ok"]:
                    print("✅ Quicksort generated and tested successfully.")
                    demo = self.sandbox.run(
                        'printf "9 1 7 3 3\\n" | python quicksort.py', timeout=5
                    )
                    print("Demo output:", demo.get("stdout", "").strip())
                    return
                logging.warning("Test failed on attempt %d: %s", attempt, res)
        raise SystemExit("Failed after 3 attempts.")

if __name__ == "__main__":
    policy = SandboxPolicy.from_yaml("policy.yaml")
    sb = DockerSandbox(
        image=os.getenv("SANDBOX_IMAGE", "ai-sandbox:py312"),
        workspace=os.path.abspath("./workdir"),
        policy=policy,
        cpus=1.0, mem="512m", pids_limit=128, network=False
    )
    QuicksortAgent(sb, AgentConfig()).run_demo()

