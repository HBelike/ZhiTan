# 豆包语音 V3 API Key 迁移记录

## 背景

豆包语音新版控制台采用单一 API Key 鉴权。项目不再使用旧版语音应用凭证，
避免将方舟 ARK Key、旧 Token 或语音 Key 混用。

## 运行链路

```text
AudioTask
  -> DoubaoTtsProvider.has_credentials()
  -> POST https://openspeech.bytedance.com/api/v3/tts/unidirectional
  -> 逐行读取 NDJSON 音频分片
  -> 写入单段音频 / ffmpeg 拼接长旁白
  -> media_assets(audio)
  -> VideoAssemblyTask 混入最终视频
```

## 配置约定

`config/app.yaml` 的 `audio` 区域固定使用：

```yaml
provider: doubao_tts_v3
api_url: https://openspeech.bytedance.com/api/v3/tts/unidirectional
api_key_env: DOUBAO_TTS_API_KEY
resource_id: seed-tts-2.0
```

实际密钥只放在项目根目录 `.env` 或部署平台的 Secret 管理中：

```text
DOUBAO_TTS_API_KEY=
```

该 Key 必须从“豆包语音 → API Key 管理”创建，不能使用火山方舟 ARK API Key。

## 请求协议

每次合成请求使用以下请求头：

```text
X-Api-Key: <豆包语音 API Key>
X-Api-Resource-Id: seed-tts-2.0
X-Api-Request-Id: <每次请求唯一 UUID>
```

请求体使用 `req_params.text`、`req_params.speaker` 和 `audio_params`。服务端按
NDJSON 返回，Provider 仅拼接每行 `data` 内的 Base64 音频，并只记录不含密钥和
Base64 的元数据。

## 可靠性与成本边界

- 长旁白按 900 UTF-8 字节优先分句，再使用 ffmpeg 无损拼接；
- API Key 缺失或云端调用失败时，`AudioTask` 可以按现有配置回退本地 SAPI / edge-tts；
- `verify_doubao_tts_v3_contract.py` 使用 Mock 响应验证请求头、请求体与 NDJSON 解析，
  不会发起云端调用，也不会产生费用；
- 首次真实调用应使用一小段旁白进行验收，确认账户已开通 `seed-tts-2.0` 后再运行完整周报。

## 本次验证

```powershell
.\.venv\Scripts\python.exe scripts\verify_doubao_tts_v3_contract.py
.\.venv\Scripts\python.exe scripts\verify_doubao_tts_chunking.py
```

前者验证 API Key 协议，后者验证长文本切分和音频拼接；两者都不读取真实密钥。

## 真实连通性验收

2026-08-08 已使用项目 `.env` 中的豆包语音 API Key 完成一次最小真实合成验证：

- 使用资源：`seed-tts-2.0`；
- 请求协议：V3 HTTP 单向流式 NDJSON；
- 音色：`zh_female_vv_uranus_bigtts`；
- 结果：成功生成 MP3 音频，证明新版 `X-Api-Key` 鉴权链路可用；
- 边界：验收日志只保留文件大小、协议、音色和用量元数据，不记录 API Key 或音频内容。

后续如需重复执行真实验收，运行：

```powershell
.\.venv\Scripts\python.exe scripts\verify_doubao_tts_v3_live.py
```
