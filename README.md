# ai-zsm · 火山人像融合 3.6 本地工具

基于火山引擎「人像融合 3.6」（`face_swap3_6`）的本机换脸工具：**CLI + 网页界面**双形态，纯 Python 标准库**零依赖**（火山 V4 HMAC-SHA256 签名为手写实现，无需官方 SDK）。

## 功能

| 子命令 | 说明 |
|---|---|
| `swap` | 命令行换脸：1~3 张素材图 + 1 张模板图，支持相似度/人脸定位/序号/水印全参数 |
| `web` | 本机网页界面（`http://127.0.0.1:8788`）：拖拽上传、缩略图管理、全参数面板（每个参数带说明）、结果预览与下载 |
| `ping` | 一键自检密钥/签名/服务开通状态 |

格式自适应（macOS，依赖系统 `sips`）：

- webp / heic（iPhone 原图）自动转 jpg
- 宽或高超 4096px 自动等比缩小（不超限零重编码损耗）

## 准备

1. 火山引擎账号实名认证 → 控制台「AI 能力」开通「**人像融合 3.6**」（1000 次免费试用）
2. [访问密钥页](https://console.volcengine.com/iam/keymanage/) 创建 AK/SK（建议 IAM 子账号、仅授视觉服务权限）
3. 写入 `~/.volc-key`（两行：AccessKeyID / SecretAccessKey，`chmod 600`），或 `export VOLC_ACCESS_KEY=... VOLC_SECRET_KEY=...`

## 用法

```bash
# 网页界面（推荐）
python3 facefusion-volc.py web            # 自动打开浏览器,仅监听 127.0.0.1

# 命令行
python3 facefusion-volc.py ping           # 自检
python3 facefusion-volc.py swap 素材.jpg 模板.jpg
python3 facefusion-volc.py swap a.jpg b.jpg -o out.jpg --similarity 0.6
python3 facefusion-volc.py swap a.jpg group.jpg --template-location 2   # 换合照里第2张脸
python3 facefusion-volc.py swap a.jpg b.jpg --logo                      # 加"AI生成"水印
```

## 参数速查

| 参数 | 取值 | 作用 |
|---|---|---|
| `source_similarity` | "0"~"1" 字符串，默认 "1" | 0=保持模板原脸，1=最大相似素材脸；0.6~0.8 兼顾 |
| `face_type` | area（默认）/ l2r / t2b | 多人脸时的序号规则：面积 / 左→右 / 上→下 |
| `merge_infos.location` | ≥1 | 素材图第几张脸 |
| `merge_infos.template_location` | ≥1 | 模板图第几张脸 |
| `logo_info` | position 0~3 / language 0~1 / 文字≤50字 | 明水印位置、语言、自定义文案 |
| 素材图数量 | 1~3 张（素材在前、模板在后） | 多脸来源；最多同时换三张脸 |

## 常见错误码

| 码 | 含义 | 处理 |
|---|---|---|
| HTTP 401 + `50400` | 服务未开通（**不是签名错**） | 控制台开通「人像融合 3.6」 |
| `50411` | 风控拦截：名人/政要照片 | 换普通人物照片 |
| `50207` | 图片尺寸超限（宽或高 >4096px） | 本工具已自动缩小，手动调用需自行处理 |
| `60102` | 未检出人脸 | 正脸、光线充足、人脸边长 ≥200px |
| 空返回 | 素材质量过低 | 换清晰正脸图 |

## 计费

一次换脸请求 = 一次调用（单次出 1 张图）；**只有成功出图才计次**（风控/无脸等失败不计）。试用档并发很低（1 级），请勿并发狂点。

## 合规

- 换真人脸须获被换脸者授权（《民法典》肖像权）
- 对外发布需显著标识 + 隐式标识（接口 `logo_info` / `aigc_meta`，遵循《人工智能生成合成内容标识办法》《互联网信息服务深度合成管理规定》）
- 素材请勿上传名人/政要照片（上游风控直接拒绝）

## 与官方文档不一致的实测行为

- 响应结果在顶层 `data.binary_data_base64`，**不是**文档写的 `Result`
- 成功码为顶层 `code: 10000`，HTTP 200 不代表成功
- `source_similarity` 文档类型为字符串

## 平台

macOS（`sips` 仅 Darwin 可用；Linux 需自行替换转码/缩放实现）。
