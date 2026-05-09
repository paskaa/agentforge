"""
Tests for AgentForge core modules.

Run with: python3 -m pytest tests/ -v
Or:       python3 tests/test_core.py
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parents[1] / "src"))


# =========================================================================
#  Tool Registry tests
# =========================================================================

class TestToolRegistry:
    def test_register_and_find(self):
        from agentforge.core.tool_registry import ToolRegistry, ToolPlugin, ToolContext

        reg = ToolRegistry()
        reg.register(ToolPlugin(
            name="test_tool",
            description="A test tool",
            handler=lambda m, c: "result",
            triggers=["hello"],
            keywords=["world"],
            priority=10,
        ))

        # Should match
        found = reg.find("hello world")
        assert len(found) == 1
        assert found[0].name == "test_tool"

        # Should not match
        found = reg.find("nothing here")
        assert len(found) == 0

    def test_execute_raw(self):
        from agentforge.core.tool_registry import ToolRegistry, ToolPlugin, ToolContext
        from pathlib import Path

        reg = ToolRegistry()
        reg.register(ToolPlugin(
            name="raw_tool",
            description="RAW output",
            handler=lambda m, c: "direct output",
            triggers=["raw"],
            raw_output=True,
        ))

        ctx = ToolContext(
            agent_id="test", agent_name="Test",
            zentao_dir=Path("/tmp"), scripts_dir=Path("/tmp"),
            agent_account="test", refresh_token=lambda: None,
        )

        flag, output = reg.execute("trigger raw", ctx)
        assert flag == "__RAW__"
        assert output == "direct output"

    def test_agent_only_filter(self):
        from agentforge.core.tool_registry import ToolRegistry, ToolPlugin

        reg = ToolRegistry()
        reg.register(ToolPlugin(
            name="liubei_only",
            description="Only for liubei",
            handler=lambda m, c: "ok",
            triggers=["dispatch"],
            agent_only="liubei",
        ))

        assert len(reg.find("dispatch", agent_id="liubei")) == 1
        assert len(reg.find("dispatch", agent_id="guanyu")) == 0

    def test_discover_tools(self):
        from agentforge.core.tool_registry import ToolRegistry, discover_tools
        import agentforge.core.builtin_tools as builtin

        reg = ToolRegistry()
        discover_tools(reg, builtin)
        assert len(reg._tools) >= 7

        # Verify priorities are descending
        priorities = [t.priority for t in reg._tools]
        assert priorities == sorted(priorities, reverse=True)


# =========================================================================
#  LLM Client tests (mock)
# =========================================================================

class TestLLMClient:
    def test_retry_logic(self):
        from agentforge.core.llm import LLMClient
        import time

        client = LLMClient(
            api_key="fake", api_base="http://localhost:1",
            model="test", max_retries=2, retry_delay=0.01,
        )

        start = time.time()
        result = client.call([{"role": "user", "content": "hi"}])
        elapsed = time.time() - start

        # Should fail (no server), but retry twice
        assert result is None
        # Retry delay: 0.01 + 0.02 = 0.03, should be under 0.5s
        assert elapsed < 0.5


# =========================================================================
#  Intent Routing tests
# =========================================================================

class TestIntentRouting:
    def test_expertise_matching(self):
        # Simulate the should_respond logic from executor
        expertise = {
            "guanyu": ["后端", "java", "api"],
            "zhaoyun": ["前端", "vue", "页面"],
            "xunyu": ["数据库", "sql", "表"],
        }

        def should_respond(agent_id, text):
            text_lower = text.lower()
            my_score = sum(1 for kw in expertise.get(agent_id, []) if kw in text_lower)
            other_max = 0
            for aid, kws in expertise.items():
                if aid == agent_id:
                    continue
                other_max = max(other_max, sum(1 for kw in kws if kw in text_lower))
            return my_score > 0 and my_score >= other_max and (my_score >= 2 or other_max == 0)

        assert should_respond("guanyu", "后端 api 接口有问题") == True
        assert should_respond("zhaoyun", "后端 api 接口有问题") == False
        assert should_respond("xunyu", "数据库 查询 慢") == True

    def test_no_match_returns_false(self):
        expertise = {"guanyu": ["后端", "java"]}
        def should_respond(agent_id, text):
            text_lower = text.lower()
            my_score = sum(1 for kw in expertise.get(agent_id, []) if kw in text_lower)
            other_max = 0
            for aid, kws in expertise.items():
                if aid == agent_id:
                    continue
                other_max = max(other_max, sum(1 for kw in kws if kw in text_lower))
            return my_score > 0 and my_score >= other_max and (my_score >= 2 or other_max == 0)

        assert should_respond("guanyu", "今天天气真好") == False


# =========================================================================
#  Dead Letter Queue tests
# =========================================================================

class TestDeadLetterQueue:
    def test_retry_then_dlq(self, monkeypatch):
        """Test that a task is retried 3 times then moved to DLQ."""
        from agentforge.core.dead_letter import DeadLetterQueue

        class FakeRedis:
            def __init__(self):
                self.streams = {}
                self.deleted = []

            def xadd(self, stream, fields):
                self.streams.setdefault(stream, []).append(fields)
                return "fake-id"

            def xdel(self, stream, msg_id):
                self.deleted.append((stream, msg_id))

            def xrange(self, stream, min, max, count):
                return [("fake-id", {"message": "test", "agent_id": "xunyu"})]

            def xlen(self, stream):
                return len(self.streams.get(stream, []))

            def xrevrange(self, stream, count):
                items = []
                for i, fields in enumerate(self.streams.get(stream, [])):
                    items.append((f"id-{i}", fields))
                return items

        fake = FakeRedis()
        dlq = DeadLetterQueue(fake)

        # First 3 failures: retry (not moved)
        for i in range(3):
            task = {"msg_id": f"msg-{i}", "_retries": str(i)}
            moved = dlq.record_failure(task, "simulated error")
            assert moved == False

        # 4th failure: moved to DLQ
        task = {"msg_id": "msg-4", "_retries": "3"}
        moved = dlq.record_failure(task, "final error")
        assert moved == True
        assert "agent-work-dlq" in fake.streams

    def test_replay(self):
        from agentforge.core.dead_letter import DeadLetterQueue

        class FakeRedis:
            def __init__(self):
                self.streams = {}
                self.deleted = []

            def xadd(self, stream, fields):
                self.streams.setdefault(stream, []).append(fields)
                return "fake-id"

            def xdel(self, stream, msg_id):
                self.deleted.append((stream, msg_id))
                return 1

            def xrange(self, stream, min, max, count):
                return [("fake-id", {
                    "message": "test", "agent_id": "xunyu",
                    "_retries": "3", "_last_error": "err",
                })]

            def xlen(self, stream):
                return len(self.streams.get(stream, []))

            def xrevrange(self, stream, count):
                return []

        fake = FakeRedis()
        dlq = DeadLetterQueue(fake)

        ok = dlq.replay("fake-id")
        assert ok == True
        assert "agent-work-queue" in fake.streams
        assert ("agent-work-dlq", "fake-id") in fake.deleted


# =========================================================================
#  Metrics tests
# =========================================================================

class TestMetrics:
    def test_counter_increment(self):
        from agentforge.core.metrics import MetricsRegistry
        reg = MetricsRegistry()
        reg.inc("tasks_processed")
        reg.inc("tasks_processed")
        reg.inc("tasks_processed", labels={"agent": "xunyu"})

        text = reg.prometheus_text()
        assert "agentforge_tasks_processed" in text
        assert "2" in text  # unlabeled counter

    def test_gauge(self):
        from agentforge.core.metrics import MetricsRegistry
        reg = MetricsRegistry()
        reg.set_gauge("active_agents", 8)

        text = reg.prometheus_text()
        assert "agentforge_active_agents" in text
        assert "8" in text

    def test_uptime(self):
        from agentforge.core.metrics import MetricsRegistry
        import time
        reg = MetricsRegistry()
        time.sleep(0.01)
        text = reg.prometheus_text()
        assert "agentforge_uptime_seconds" in text


# =========================================================================
#  Tool Executor tests
# =========================================================================

class TestToolExecutor:
    def test_run_script_safe(self):
        from agentforge.core.tool_executor import run_script
        import tempfile, os

        # Create a small test script
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as f:
            f.write("#!/bin/bash\nexit 0\n")
            tmpname = f.name
        os.chmod(tmpname, 0o755)

        rc, out, err = run_script(tmpname, timeout=5)
        os.unlink(tmpname)
        assert rc == 0

    def test_injection_prevented(self):
        """Verify that args are passed as literal argv, not interpreted by shell."""
        from agentforge.core.tool_executor import run_script
        import tempfile, os

        # Create a script that echoes its args
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as f:
            f.write('#!/bin/bash\necho "arg1=$1"\n')
            tmpname = f.name
        os.chmod(tmpname, 0o755)

        # Pass a potentially malicious arg with semicolon
        rc, out, err = run_script(tmpname, "; rm -rf /", timeout=5)
        os.unlink(tmpname)

        # The semicolon is passed literally as $1, not executed by shell
        assert rc == 0
        assert "arg1=; rm -rf /" in out


# =========================================================================
#  Config tests
# =========================================================================

class TestConfig:
    def test_agent_name(self):
        from agentforge.config import Config
        cfg = Config()
        assert cfg.get_agent_name("zhugeliang") == "诸葛亮"
        assert cfg.get_agent_name("unknown") == "unknown"

    def test_agent_account(self):
        from agentforge.config import Config
        cfg = Config()
        assert cfg.get_agent_account("xunyu") == "xunyu"

    def test_expertise(self):
        from agentforge.config import Config
        cfg = Config()
        assert "数据库" in cfg.expertise["xunyu"]
        assert "后端" in cfg.expertise["guanyu"]

    def test_redis_kwargs(self):
        from agentforge.config import Config
        cfg = Config()
        kw = cfg.redis_kwargs
        assert "host" in kw
        assert "port" in kw
        assert kw["decode_responses"] == True


# =========================================================================
#  Runner
# =========================================================================

if __name__ == "__main__":
    # Simple test runner (no pytest dependency needed)
    import traceback

    test_classes = [
        TestToolRegistry, TestLLMClient, TestIntentRouting,
        TestDeadLetterQueue, TestMetrics, TestToolExecutor, TestConfig,
    ]

    passed = 0
    failed = 0

    for cls in test_classes:
        instance = cls()
        for name in dir(instance):
            if name.startswith("test_"):
                method = getattr(instance, name)
                try:
                    # Check if method accepts monkeypatch
                    import inspect
                    sig = inspect.signature(method)
                    if "monkeypatch" in sig.parameters:
                        # Skip monkeypatch tests in simple runner
                        continue
                    method()
                    passed += 1
                    print(f"  PASS  {cls.__name__}.{name}")
                except Exception as e:
                    failed += 1
                    print(f"  FAIL  {cls.__name__}.{name}: {e}")
                    traceback.print_exc()

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed, {passed+failed} total")
    if failed:
        print("SOME TESTS FAILED")
        sys.exit(1)
    else:
        print("ALL TESTS PASSED")
