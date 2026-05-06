"""
Workflow Engine - Multi-step serial/parallel/approval workflows.
"""

import json
import os
import time
import redis
from datetime import datetime

from agentforge.network.feishu import FeishuClient


class WorkflowEngine:
    def __init__(self):
        self.redis = redis.Redis(
            host=os.environ.get("REDIS_HOST", "127.0.0.1"),
            port=int(os.environ.get("REDIS_PORT", "6379")),
            decode_responses=True,
        )
        self.task_queue = "agent-work-queue"
        self.wf_state = "workflow-state"
        self.group_chat_id = os.environ.get("FEISHU_GROUP_CHAT_ID", "")
        self.creds_file = os.environ.get("FEISHU_CREDENTIALS_FILE", "./config/feishu_credentials.json")

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

    def _get_feishu(self, agent_id="zhugeliang"):
        with open(self.creds_file) as f:
            creds = json.load(f)
        app = creds["agents"].get(agent_id, next(iter(creds["agents"].values())))
        return FeishuClient(app["appId"], app["appSecret"], self.group_chat_id)

    def create(self, name, description, steps):
        wf = {"name": name, "description": description, "steps": steps,
              "created_at": datetime.now().isoformat(), "status": "pending",
              "current_step": 0, "results": {}}
        wf_id = f"wf-{int(time.time())}"
        self.redis.hset(self.wf_state, wf_id, json.dumps(wf))
        return wf_id

    def execute_step(self, wf_id, step):
        wf = json.loads(self.redis.hget(self.wf_state, wf_id))
        st = step.get("type")

        if st == "agent":
            aid = step.get("agent"); msg = step.get("message", "")
            self.redis.xadd(self.task_queue, {
                "agent_id": aid, "agent_name": self.agents.get(aid, {}).get("name", aid),
                "message": msg, "source": "workflow-engine",
                "task_id": f"{wf_id}-step-{wf['current_step']}",
                "timestamp": datetime.now().isoformat(), "workflow_id": wf_id,
            })
            wf["current_step"] += 1; wf["status"] = "running"
            feishu = self._get_feishu(aid)
            feishu.send(f"工作流引擎\n\n任务分配给 **{self.agents.get(aid, {}).get('name', aid)}**\n\n{msg[:200]}...",
                        agent_name=self.agents.get(aid, {}).get("name", aid))

        elif st == "parallel":
            for aid in step.get("agents", []):
                self.redis.xadd(self.task_queue, {
                    "agent_id": aid, "message": step.get("message", ""),
                    "source": "workflow-engine", "timestamp": datetime.now().isoformat(),
                    "workflow_id": wf_id,
                })
            wf["current_step"] += 1

        elif st == "review":
            wf["status"] = "waiting_review"

        self.redis.hset(self.wf_state, wf_id, json.dumps(wf))
        return wf

    def run(self, wf_id):
        wf = json.loads(self.redis.hget(self.wf_state, wf_id))
        for step in wf.get("steps", []):
            self.execute_step(wf_id, step)
            time.sleep(30)
        wf["status"] = "completed"
        self.redis.hset(self.wf_state, wf_id, json.dumps(wf))
        return wf

    def list_workflows(self):
        wfs = self.redis.hgetall(self.wf_state)
        for wid, wdata in wfs.items():
            w = json.loads(wdata)
            print(f"  {wid}: {w['name']} ({w['status']})")
        return wfs
