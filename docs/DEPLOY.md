# 部署指南

## 首次部署

```bash
cd /root/agentforge
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 配置
cp .env.example .env          # 编辑填写真实值
cp config/feishu_credentials.json.example config/feishu_credentials.json

# 安装 systemd
sudo cp deploy/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
```

## 启动全部服务

```bash
AGENTS="zhugeliang liubei guanyu zhaoyun xunyu zhangfei huatuo chenlin"
for a in $AGENTS; do
  systemctl enable --now agentforge-executor@$a
  systemctl enable --now agentforge-ws@$a
done
systemctl enable --now agentforge-scheduler
```

## 健康检查

```bash
# 状态
systemctl list-units --type=service --state=running | grep agentforge

# 日志
journalctl -u agentforge-executor@xunyu -f

# 测试
cd /root/agentforge && venv/bin/python3 tests/test_core.py
```

## 回滚到旧版

```bash
# 停新服务
systemctl stop agentforge-executor@* agentforge-ws@* agentforge-scheduler

# 启旧服务（如果旧 unit 还在）
systemctl start agent-executor@xunyu agent-ws@xunyu
```

## 更新代码后

```bash
cd /root/agentforge
git pull
# 重启全部 executor（ws_listener 和 scheduler 不用重启）
AGENTS="zhugeliang liubei guanyu zhaoyun xunyu zhangfei huatuo chenlin"
for a in $AGENTS; do systemctl restart agentforge-executor@$a; done
```
