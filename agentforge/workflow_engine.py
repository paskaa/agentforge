#!/usr/bin/env python3
"""
AgentForge Workflow Engine - Lightweight Multi-Agent Workflow

Supports complex workflows:
- Task decomposition and assignment
- Parallel execution
- Conditional branching
- Approval process
- Agent coordination

Start: python3 -m agentforge.workflow_engine
"""

import json
import os
import time
import redis
import requests
from datetime import datetime
from pathlib import Path


class WorkflowEngine:
    def __init__(self, config=None):
        if config:
            self.redis = redis.Redis(
                host=config.redis_host,
                port=config.redis_port,
                password=config.redis_password,
                decode_responses=True,
            )
            self.feishu_credentials_file = config.feishu_credentials_file
            self.feishu_group_chat_id = config.feishu_group_chat_id
        else:
            self.redis = redis.Redis(
                host=os.environ.get("REDIS_HOST", "127.0.0.1"),
                port=int(os.environ.get("REDIS_PORT", "6379")),
                decode_responses=True,
            )
            self.feishu_credentials_file = os.environ.get(
                "FEISHU_CREDENTIALS_FILE", "./config/feishu_credentials.json"
            )
            self.feishu_group_chat_id = os.environ.get(
                "FEISHU_GROUP_CHAT_ID", ""
            )

        self.task_queue = "agent-work-queue"
        self.workflow_state = "workflow-state"

        self.agents = {
            "zhugeliang": {"role": "架构师", "name": "诸葛亮"},
            "liubei": {"role": "项目经理", "name": "刘备"},
            "guanyu": {"role": "后端开发", "name": "关羽"},
            "zhaoyun": {"role": "前端开发", "name": "赵云"},
            "xunyu": {"role": "DBA", "name": "荀彧"},
            "zhangfei": {"role": "测试专家", "name": "张飞"},
            "huatuo": {"role": "产品经理", "name": "华佗"},
            "chenlin": {"role": "文档管理", "name": "陈琳"},
        }

    def _get_feishu_token(self, agent_id="zhugeliang"):
        """Get Feishu access token"""
        try:
            with open(self.feishu_credentials_file) as f:
                creds = json.load(f)
            app = creds["agents"].get(agent_id, next(iter(creds["agents"].values())))
            resp = requests.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={
                    "app_id": app["appId"],
                    "app_secret": app["appSecret"],
                },
                headers={"Content-Type": "application/json; charset=utf-8"},
            )
            return resp.json().get("tenant_access_token")
        except Exception:
            return None

    def send_feishu(self, text, agent_id="zhugeliang"):
        """Send Feishu message to group"""
        token = self._get_feishu_token(agent_id)
        if not token:
            return False

        agent_name = self.agents.get(agent_id, {}).get("name", agent_id)
        content = json.dumps({
            "config": {"wide_screen_mode": True},
            "elements": [
                {"tag": "markdown", "content": text},
                {
                    "tag": "note",
                    "elements": [
                        {"tag": "plain_text", "content": f"{agent_name} 工作流引擎"}
                    ],
                },
            ],
        })

        try:
            resp = requests.post(
                "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
                json={
                    "receive_id": self.feishu_group_chat_id,
                    "msg_type": "interactive",
                    "content": content,
                },
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
            )
            return resp.status_code == 200
        except Exception:
            return False

    def create_workflow(self, name, description, steps):
        """Create a new workflow"""
        workflow = {
            "name": name,
            "description": description,
            "steps": steps,
            "created_at": datetime.now().isoformat(),
            "status": "pending",
            "current_step": 0,
            "results": {},
        }

        workflow_id = f"wf-{int(time.time())}"
        self.redis.hset(self.workflow_state, workflow_id, json.dumps(workflow))

        print(f"Workflow created: {workflow_id}")
        return workflow_id

    def execute_step(self, workflow_id, step):
        """Execute a workflow step"""
        workflow = json.loads(self.redis.hget(self.workflow_state, workflow_id))
        step_type = step.get("type")

        if step_type == "agent":
            agent_id = step.get("agent")
            message = step.get("message", "")

            task_id = f"{workflow_id}-step-{workflow['current_step']}"
            self.redis.xadd(
                self.task_queue,
                {
                    "agent_id": agent_id,
                    "agent_name": self.agents.get(agent_id, {}).get("name", agent_id),
                    "message": message,
                    "source": "workflow-engine",
                    "task_id": task_id,
                    "timestamp": datetime.now().isoformat(),
                    "workflow_id": workflow_id,
                },
            )

            print(f"  Task sent to {agent_id}")
            self.send_feishu(
                f"工作流引擎\n\n任务分配给 **{self.agents.get(agent_id, {}).get('name', agent_id)}**\n\n{message[:200]}..."
            )

            workflow["current_step"] += 1
            workflow["status"] = "running"
            self.redis.hset(self.workflow_state, workflow_id, json.dumps(workflow))

        elif step_type == "parallel":
            agents = step.get("agents", [])
            message = step.get("message", "")

            for agent_id in agents:
                task_id = f"{workflow_id}-parallel-{agent_id}"
                self.redis.xadd(
                    self.task_queue,
                    {
                        "agent_id": agent_id,
                        "agent_name": self.agents.get(agent_id, {}).get(
                            "name", agent_id
                        ),
                        "message": message,
                        "source": "workflow-engine",
                        "task_id": task_id,
                        "timestamp": datetime.now().isoformat(),
                        "workflow_id": workflow_id,
                    },
                )
                print(f"  Parallel task sent to {agent_id}")

            self.send_feishu(
                f"工作流引擎\n\n并行任务分配给 {len(agents)} 个 agent\n\n{message[:200]}..."
            )

            workflow["current_step"] += 1
            self.redis.hset(self.workflow_state, workflow_id, json.dumps(workflow))

        elif step_type == "review":
            print("  Review step - waiting for approval")
            self.send_feishu(
                "工作流引擎\n\n**审批等待**\n\n请人工审批后继续..."
            )
            workflow["status"] = "waiting_review"
            self.redis.hset(self.workflow_state, workflow_id, json.dumps(workflow))

        return workflow

    def run_workflow(self, workflow_id):
        """Run a complete workflow"""
        workflow = json.loads(self.redis.hget(self.workflow_state, workflow_id))
        steps = workflow.get("steps", [])

        print(f"Running workflow: {workflow['name']}")
        self.send_feishu(
            f"工作流引擎\n\n启动工作流：**{workflow['name']}**\n\n{workflow.get('description', '')}"
        )

        for i, step in enumerate(steps):
            print(f"  Step {i+1}/{len(steps)}: {step.get('type')} -> {step.get('agent', 'N/A')}")
            self.execute_step(workflow_id, step)
            time.sleep(30)

        workflow["status"] = "completed"
        self.redis.hset(self.workflow_state, workflow_id, json.dumps(workflow))

        print(f"Workflow completed: {workflow_id}")
        self.send_feishu(
            f"工作流引擎\n\n工作流完成：**{workflow['name']}**"
        )

        return workflow

    def list_workflows(self):
        """List all workflows"""
        workflows = self.redis.hgetall(self.workflow_state)
        print(f"Total workflows: {len(workflows)}")

        for wf_id, wf_data in workflows.items():
            wf = json.loads(wf_data)
            print(f"  {wf_id}: {wf['name']} ({wf['status']})")

        return workflows


def create_bug_workflow():
    """Create example bug handling workflow"""
    engine = WorkflowEngine()

    workflow_id = engine.create_workflow(
        name="Bug 处理流程",
        description="张飞报告 Bug -> 关羽修复 -> 张飞验证 -> 诸葛亮归档",
        steps=[
            {
                "type": "agent",
                "agent": "zhangfei",
                "message": "请描述禅道中待处理的 Bug，包括现象、复现步骤和预期结果",
            },
            {
                "type": "agent",
                "agent": "guanyu",
                "message": "请修复张飞报告的 Bug，完成后报告修复内容",
            },
            {
                "type": "agent",
                "agent": "zhangfei",
                "message": "请验证关羽的修复结果，确认 Bug 是否已解决",
            },
            {
                "type": "agent",
                "agent": "zhugeliang",
                "message": "请归档本次 Bug 处理过程",
            },
        ],
    )

    return workflow_id


if __name__ == "__main__":
    engine = WorkflowEngine()

    if len(sys.argv) > 1:
        command = sys.argv[1]

        if command == "create":
            workflow_id = create_bug_workflow()
            print(f"\nCreated workflow: {workflow_id}")

        elif command == "run":
            workflow_id = sys.argv[2] if len(sys.argv) > 2 else None
            if workflow_id:
                engine.run_workflow(workflow_id)
            else:
                print("Usage: python3 -m agentforge.workflow_engine run <workflow_id>")

        elif command == "list":
            engine.list_workflows()

        else:
            print("Unknown command. Use: create, run, list")
    else:
        print("轻量级多 Agent 工作流引擎")
        print("Usage:")
        print("  python3 -m agentforge.workflow_engine create  - 创建示例工作流")
        print("  python3 -m agentforge.workflow_engine run <id> - 运行工作流")
        print("  python3 -m agentforge.workflow_engine list     - 列出所有工作流")
