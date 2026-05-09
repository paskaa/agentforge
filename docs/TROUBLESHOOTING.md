# 故障排查

## Agent 不停发消息

**症状**：某个 Agent 在飞书群疯狂刷屏。

**根因**：ACK 失败 → 消息 PENDING → 无限重放。

**排查**：
```bash
# 1. 看日志确认 ACK 错误
journalctl -u agentforge-executor@liubei --no-pager -n 50 | grep "ACK error"

# 2. 检查 PENDING 堆积
cd /root/agentforge && venv/bin/python3 -c "
import redis; r = redis.Redis(host='127.0.0.1', port=16379, decode_responses=True)
print(r.xpending('agent-work-queue', 'liubei-workers'))
"

# 3. 清理 PENDING
systemctl stop agentforge-executor@<id>
# 见下方"清理 PENDING 队列"
```

**修复**：确认 `_redis_id` 未被飞书 `msg_id` 覆盖（`#ACK_BUG_20260508`）。

## Agent 启动后没反应

**排查**：
```bash
# 1. 检查进程
systemctl status agentforge-executor@xunyu

# 2. 看日志
journalctl -u agentforge-executor@xunyu -f

# 3. 检查 Redis
venv/bin/python3 -c "import redis; r=redis.Redis(host='127.0.0.1',port=16379); print(r.ping())"
```

## Claude Code 修复失败

**症状**：飞书回复"修复受阻"。

**常见原因**：
1. Claude Code 没找到可修改代码（改错文件/目标源码不在 repo）
2. API 429（配额不足）→ 等待 30 分钟自动重试
3. git 冲突 → 手动解决后重跑

**排查**：
```bash
# 看 Claude Code 输出
journalctl -u agentforge-executor@zhaoyun | grep "claude-code\|修复受阻"
```

## 清理 PENDING 队列

```bash
cd /root/agentforge && venv/bin/python3 -c "
import redis
r = redis.Redis(host='127.0.0.1', port=16379, decode_responses=True)
stream, group = 'agent-work-queue', 'liubei-workers'
pending = r.xpending(stream, group)
total = pending.get('pending', 0) if isinstance(pending, dict) else 0
if total > 0:
    result = r.xpending_range(stream, group, min='-', max='+', count=total)
    ids = [item['message_id'] for item in result]
    r.xclaim(stream, group, 'cleanup', 0, ids)
    for mid in ids: r.xack(stream, group, mid)
    print(f'Cleaned {len(ids)}')
print('Done')
"
```
