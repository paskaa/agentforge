# Bug 修复管线

## 全自动流程

```
Self Boot Check
  └→ 扫描禅道 Bug → 发飞书通知 → 派 3 个修复任务到 Redis
       │
       ▼
  Executor 收到 self_boot_check 任务
       │
       ├→ 刘备（PM）：跳过修复，只回复"负责分析"
       │
       └→ 其他 Agent：
            ├→ 查询 Bug 详情
            ├→ 异步调用 Claude Code 修复
            ├→ git commit + push
            ├→ zentao-write-bug resolve
            └→ Redis → 张飞测试
                 │
                 ▼
            张飞（pipeline_fix_done）：
              ├→ 查询 Bug 状态
              ├→ zentao-write-bug assign 回提出人
              ├→ 飞书通知
              └→ Redis → 华佗验收
                   │
                   ▼
              华佗（pipeline_test_done）：
                ├→ zentao-write-bug resolve
                ├→ 飞书通知提出人验证
                └→ 管线结束
```

## 关键约束

- **智能体不能关闭 Bug**：只有人类发起人可以关闭
- **刘备不修 Bug**：PM 角色跳过 boot_check
- **Claude Code 异步**：不阻塞主循环
- **超时保护**：Claude Code 最多 3 小时（10800s）

## 管线代码

- `core/pipeline.py` — `handle_pipeline_test()` / `handle_pipeline_verify()` / `handle_self_boot()`
- `core/executor.py` — `handle_task()` 中的 `source` 路由
