# 🌟 Hướng dẫn Sử dụng Flow Kit FastAPI API

Tài liệu này cung cấp hướng dẫn chi tiết về các API được cung cấp bởi server FastAPI của **Flow Kit** (chạy mặc định tại `http://127.0.0.1:8100`).

---

## 🏗️ Kiến Trúc Hệ Thống

Hệ thống Flow Kit hoạt động như một Proxy bắc cầu cho hai thành phần chính:
1. **Google Flow Proxy**:
   * **FastAPI Server** (`http://127.0.0.1:8100`): Cung cấp các REST API cho client gọi.
   * **WebSocket Server** (`ws://127.0.0.1:9222`): Chrome Extension kết nối vào đây.
   * **Chrome Extension (Google Flow)**: Nhận lệnh từ WebSocket, thực hiện request thực tế trên trang Google Flow (sử dụng Cookie/Bearer Token `ya29.*` sẵn có của trình duyệt) và trả kết quả về thông qua HTTP Callback (`/api/ext/callback`). Không cần cài đặt `GOOGLE_API_KEY`.
2. **OmniVoice TTS Proxy**:
   * Chuyển tiếp (proxy) các cuộc gọi tổng hợp và nhân bản giọng nói đến server **OmniVoice** chạy trên Google Colab (sử dụng ngrok/localtunnel).

---

## 🚦 Endpoint Kiểm Tra Trạng Trái & Hệ Thống

### 1. Health Check
Kiểm tra trạng thái hoạt động của FastAPI server và kết nối của Chrome Extension.

* **URL:** `/health`
* **Method:** `GET`
* **Response Ví dụ:**
  ```json
  {
    "status": "ok",
    "version": "0.2.0",
    "extension_connected": true,
    "ws": {
      "connected": true,
      "connects": 1,
      "disconnects": 0,
      "uptime_s": 120
    }
  }
  ```

### 2. Extension Status
Kiểm tra kết nối của Extension và xem Flow Key đã được capture hay chưa.

* **URL:** `/api/flow/status`
* **Method:** `GET`
* **Response Ví dụ:**
  ```json
  {
    "connected": true,
    "flow_key_present": true
  }
  ```

### 3. Xem Số Lượng Credits (Số dư tài khoản Flow)
Lấy thông tin credits hiện tại của tài khoản Google Flow.

* **URL:** `/api/flow/credits`
* **Method:** `GET`
* **Response Ví dụ:**
  ```json
  {
    "credits": 420
  }
  ```

---

## 📁 Quản Lý Project (Dự Án)

Các API này giúp bạn quản lý các project trên Google Flow.

### 1. Tạo Project Mới
Tạo một project trống để gom nhóm các hình ảnh và video được tạo ra.

* **URL:** `/api/flow/create-project`
* **Method:** `POST`
* **Request Body (JSON):**
  | Field | Type | Required | Default | Description |
  | :--- | :--- | :---: | :---: | :--- |
  | `project_title` | `str` | Yes | | Tên hiển thị của project |
  | `tool_name` | `str` | No | `"PINHOLE"` | Tool Google Flow sử dụng |

* **Request Example:**
  ```json
  {
    "project_title": "Dự án Marketing Q2"
  }
  ```

* **Response Ví dụ:**
  ```json
  {
    "result": {
      "data": {
        "json": {
          "result": {
            "projectId": "c4898aaf-606f-47cb-b61e-52b49053ac7e",
            "projectInfo": {
              "projectTitle": "Dự án Marketing Q2"
            }
          },
          "status": 200,
          "statusText": "OK"
        }
      }
    }
  }
  ```

### 2. Lấy Danh Sách Projects
* **URL:** `/api/flow/projects`
* **Method:** `GET`
* **Description:** Lấy danh sách tất cả các projects hiện có trên tài khoản Google Flow.

### 3. Chi Tiết Project
* **URL:** `/api/flow/project/{project_id}`
* **Method:** `GET`
* **Description:** Lấy chi tiết thông tin và danh sách các workflow/media nằm trong một project xác định.

### 4. Xóa Project
* **URL:** `/api/flow/delete-project/{project_id}`
* **Method:** `GET`
* **Description:** Xóa project tương ứng trên Google Flow.

---

## 🎨 Tạo và Biên Tập Hình Ảnh (Image Generation & Edit)

### 1. Tạo Ảnh Từ Prompt (Text-to-Image)
Tạo hình ảnh mới dựa trên mô tả văn bản (Prompt).

* **URL:** `/api/flow/generate-image`
* **Method:** `POST`
* **Request Body (JSON):**
  | Field | Type | Required | Default | Description |
  | :--- | :--- | :---: | :---: | :--- |
  | `prompt` | `str` | Yes | | Nội dung mô tả hình ảnh muốn tạo |
  | `project_id` | `str` | Yes | | ID của project chứa ảnh |
  | `aspect_ratio` | `str` | No | `"IMAGE_ASPECT_RATIO_PORTRAIT"` | Tỉ lệ ảnh (`IMAGE_ASPECT_RATIO_PORTRAIT`, `IMAGE_ASPECT_RATIO_LANDSCAPE`, `IMAGE_ASPECT_RATIO_SQUARE`) |
  | `user_paygate_tier` | `str` | No | `"PAYGATE_TIER_ONE"` | Tier tài khoản (`PAYGATE_TIER_ONE`, `PAYGATE_TIER_TWO`) |
  | `character_media_ids` | `list[str]` | No | `null` | Danh sách ID ảnh nhân vật mẫu (nếu muốn giữ nhân vật) |

* **Request Example:**
  ```json
  {
    "prompt": "A cinematic photo of a red fox sitting in a snowy forest at sunrise, soft light",
    "project_id": "c4898aaf-606f-47cb-b61e-52b49053ac7e",
    "aspect_ratio": "IMAGE_ASPECT_RATIO_PORTRAIT"
  }
  ```

* **Response Ví dụ:**
  ```json
  {
    "media": [
      {
        "name": "b214b800-b61a-4753-9596-a918d830fd91",
        "image": {
          "generatedImage": {
            "mediaId": "b214b800-b61a-4753-9596-a918d830fd91",
            "fifeUrl": "https://flow-content.google/image/b214b800-b61a-4753-9596-a918d830fd91?Expires=...",
            "aspectRatio": "IMAGE_ASPECT_RATIO_PORTRAIT"
          }
        }
      }
    ],
    "workflows": [ ... ]
  }
  ```

### 2. Biên Tập Ảnh (Edit Image / Image-to-Image)
Chỉnh sửa một hình ảnh hiện có bằng cách áp dụng prompt mới.

* **URL:** `/api/flow/edit-image`
* **Method:** `POST`
* **Request Body (JSON):**
  | Field | Type | Required | Default | Description |
  | :--- | :--- | :---: | :---: | :--- |
  | `prompt` | `str` | Yes | | Hướng dẫn chỉnh sửa |
  | `source_media_id` | `str` | Yes | | ID của ảnh gốc |
  | `project_id` | `str` | Yes | | ID của project |
  | `aspect_ratio` | `str` | No | `"IMAGE_ASPECT_RATIO_PORTRAIT"` | Tỉ lệ ảnh |
  | `user_paygate_tier` | `str` | No | `"PAYGATE_TIER_ONE"` | Tier tài khoản |

### 3. Tải Lên Ảnh Gốc (Upload Image)
Tải một file ảnh từ máy cục bộ lên Google Flow để làm tài nguyên sinh video hoặc tham chiếu.

* **URL:** `/api/flow/upload-image`
* **Method:** `POST`
* **Request Body (JSON):**
  | Field | Type | Required | Default | Description |
  | :--- | :--- | :---: | :---: | :--- |
  | `file_path` | `str` | Yes | | Đường dẫn tuyệt đối đến file ảnh trên ổ cứng của server |
  | `project_id` | `str` | No | `""` | ID của project |
  | `file_name` | `str` | No | `"image.png"` | Tên file muốn hiển thị |

* **Request Example:**
  ```json
  {
    "file_path": "C:/Users/sp/Pictures/landscape.png",
    "project_id": "c4898aaf-606f-47cb-b61e-52b49053ac7e"
  }
  ```

* **Response Ví dụ:**
  ```json
  {
    "media_id": "e27b56f2-c9ad-4794-ad1e-cecd8abf4731",
    "raw": { ... }
  }
  ```

### 4. Đổi Tên Hiển Thị Của Media (Rename Media)
* **URL:** `/api/flow/change-displayname`
* **Method:** `PATCH`
* **Request Body (JSON):**
  ```json
  {
    "media_id": "b214b800-b61a-4753-9596-a918d830fd91",
    "project_id": "c4898aaf-606f-47cb-b61e-52b49053ac7e",
    "display_name": "Fox in snow v2"
  }
  ```

---

## 🎬 Tạo Video (Video Generation)

Sinh video trên Google Flow là tiến trình bất đồng bộ. API sẽ trả về thông tin danh sách các **`operations`** (tác vụ đang chạy). Bạn phải sử dụng API kiểm tra trạng thái để poll cho đến khi video hoàn thành.

### 1. Tạo Video Từ Ảnh Bắt Đầu (Image-to-Video)
Sinh video ngắn từ một hình ảnh tĩnh làm điểm xuất phát.

* **URL:** `/api/flow/generate-video`
* **Method:** `POST`
* **Request Body (JSON):**
  | Field | Type | Required | Default | Description |
  | :--- | :--- | :---: | :---: | :--- |
  | `start_image_media_id` | `str` | Yes | | ID ảnh nguồn bắt đầu |
  | `prompt` | `str` | Yes | | Prompt mô tả hành động diễn ra trong video |
  | `project_id` | `str` | Yes | | ID project |
  | `scene_id` | `str` | Yes | | ID phân cảnh tự định nghĩa |
  | `aspect_ratio` | `str` | No | `"VIDEO_ASPECT_RATIO_PORTRAIT"` | Tỉ lệ video (`VIDEO_ASPECT_RATIO_PORTRAIT`, `VIDEO_ASPECT_RATIO_LANDSCAPE`) |
  | `end_image_media_id` | `str` | No | `null` | ID ảnh kết thúc (nếu có, để sinh video chuyển cảnh) |
  | `user_paygate_tier` | `str` | No | `"PAYGATE_TIER_ONE"` | Tier tài khoản |

* **Request Example:**
  ```json
  {
    "start_image_media_id": "b214b800-b61a-4753-9596-a918d830fd91",
    "prompt": "The fox slowly blinks and tilts its head, snow gently falling around it",
    "project_id": "c4898aaf-606f-47cb-b61e-52b49053ac7e",
    "scene_id": "scene_01"
  }
  ```

### 2. Tạo Video Từ Nhiều Ảnh Tham Chiếu (Reference-to-Video)
Sinh video sử dụng nhiều hình ảnh làm mẫu tham chiếu.

* **URL:** `/api/flow/generate-video-refs`
* **Method:** `POST`
* **Request Body (JSON):**
  | Field | Type | Required | Default | Description |
  | :--- | :--- | :---: | :---: | :--- |
  | `reference_media_ids` | `list[str]` | Yes | | Danh sách ID của các ảnh mẫu tham chiếu |
  | `prompt` | `str` | Yes | | Prompt mô tả hành động |
  | `project_id` | `str` | Yes | | ID project |
  | `scene_id` | `str` | Yes | | ID phân cảnh tự định nghĩa |
  | `aspect_ratio` | `str` | No | `"VIDEO_ASPECT_RATIO_PORTRAIT"` | Tỉ lệ video |
  | `user_paygate_tier` | `str` | No | `"PAYGATE_TIER_ONE"` | Tier tài khoản |

### 3. Nâng Cấp Chất Lượng Video (Upscale Video)
Tăng chất lượng/độ phân giải của video lên tối đa 4K.

* **URL:** `/api/flow/upscale/video`
* **Method:** `POST`
* **Request Body (JSON):**
  | Field | Type | Required | Default | Description |
  | :--- | :--- | :---: | :---: | :--- |
  | `media_id` | `str` | Yes | | ID của video muốn upscale |
  | `scene_id` | `str` | Yes | | ID phân cảnh |
  | `aspect_ratio` | `str` | No | `"VIDEO_ASPECT_RATIO_PORTRAIT"` | Tỉ lệ video |
  | `resolution` | `str` | No | `"VIDEO_RESOLUTION_4K"` | Độ phân giải mong muốn (`VIDEO_RESOLUTION_4K`, `VIDEO_RESOLUTION_1080P`) |

---

## ⏳ Theo Dõi Trạng Thái Tác Vụ (Operations)

### 1. Kiểm Tra Trạng Thái Tác Vụ Đang Chạy
Dùng để kiểm tra xem tiến trình sinh video/upscale đã hoàn thành hay chưa.

* **URL:** `/api/flow/check-status`
* **Method:** `POST`
* **Request Body (JSON):**
  ```json
  {
    "operations": [
      {
        "name": "operations/c4898aaf-606f-47cb-b61e-52b49053ac7e/folders/scene_01/operations/123456"
      }
    ]
  }
  ```

---

## 🔗 Truy Xuất Link Trực Tiếp (Get Direct Media URL)

Các URL hình ảnh và video được trả về từ Google Flow thường chứa chữ ký GCS ngắn hạn (thường hết hạn sau vài giờ). API này giúp lấy thông tin cập nhật mới nhất cùng đường dẫn tải trực tiếp.

### 1. Lấy Link Trực Tiếp & Metadata Mới Nhất
Lấy dữ liệu cập nhật và link download đã được ký mới nhất cho hình ảnh/video.

* **URL:** `/api/flow/media/{primary_media_id}`
* **Method:** `GET`
* **Parameters:** `primary_media_id` (ID của file ảnh/video)
* **Response Ví dụ:**
  ```json
  {
    "url": "https://flow-content.google/video/e27b56f2-c9ad-4794-ad1e-cecd8abf4731?Expires=1781638324&KeyName=labs-flow-prod-cdn-key&Signature=eRme-...",
    "redirected": true
  }
  ```

---

## 🎙️ Tổng Hợp Giọng Nói & Nhân Bản Giọng (OmniVoice TTS Proxy)

Các API này chuyển tiếp các yêu cầu tổng hợp giọng nói đến server **OmniVoice** chạy trên Google Colab. Vì URL Colab (qua ngrok/localtunnel) thay đổi theo từng phiên, bạn có thể thiết lập URL runtime qua API `/api/tts/config`.

### 1. Xem Base URL OmniVoice Hiện Tại
* **URL:** `/api/tts/config`
* **Method:** `GET`
* **Response Ví dụ:**
  ```json
  {
    "base_url": "https://random-subdomain.ngrok-free.app"
  }
  ```

### 2. Thiết Lập Base URL OmniVoice
Cập nhật URL public của Colab lúc runtime khi bạn khởi chạy một phiên làm việc mới.

* **URL:** `/api/tts/config`
* **Method:** `PUT`
* **Request Body (JSON):**
  ```json
  {
    "base_url": "https://your-new-colab-url.ngrok-free.app"
  }
  ```

### 3. Kiểm Tra Health Của Server OmniVoice
Xem server OmniVoice trên Colab đã sẵn sàng chưa và tình trạng nạp model TTS.

* **URL:** `/api/tts/health`
* **Method:** `GET`

### 4. Tổng Hợp Giọng Nói (Synthesize Text-to-Speech)
Tổng hợp văn bản thành tệp giọng nói (WAV dạng base64).

* **URL:** `/api/tts/synthesize`
* **Method:** `POST`
* **Request Body (JSON):**
  | Field | Type | Required | Default | Description |
  | :--- | :--- | :---: | :---: | :--- |
  | `text` | `str` | Yes | | Nội dung văn bản cần đọc |
  | `voice_id` | `int` | No | `0` | ID của giọng nói custom đã lưu |
  | `voice` | `str` | No | `null` | Tệp âm thanh mẫu WAV/MP3 ở dạng base64 để nhân bản động (Dynamic Cloning) |
  | `speed` | `float` | No | `1.0` | Tốc độ nói của giọng |
  | `instruct` | `str` | No | `null` | Hướng dẫn cảm xúc/giọng điệu (Ví dụ: "excited", "whispering") |

* **Request Example (Sử dụng giọng mẫu sẵn có):**
  ```json
  {
    "text": "Xin chào, chào mừng bạn đến với hệ thống Flow Kit tự động.",
    "voice_id": 1,
    "speed": 1.05
  }
  ```

* **Request Example (Nhân bản động trực tiếp):**
  ```json
  {
    "text": "Giọng nói này được nhân bản trực tiếp từ tệp âm thanh gửi kèm.",
    "voice": "UklGRu...[base64-wav-data]...",
    "speed": 1.0
  }
  ```

* **Response Ví dụ:**
  ```json
  {
    "audio": "UklGRu...[base64-wav-output]...",
    "status": "success",
    "msg": "Synthesis completed successfully"
  }
  ```

### 5. Danh Sách Các Giọng Custom Đã Đăng Ký
Lấy danh sách các giọng nói mẫu mà bạn đã thêm vào server OmniVoice.

* **URL:** `/api/tts/voices`
* **Method:** `GET`

### 6. Đăng Ký Thêm Giọng Custom Mới (Add Custom Voice)
* **URL:** `/api/tts/voices`
* **Method:** `POST`
* **Request Body (JSON):**
  | Field | Type | Required | Default | Description |
  | :--- | :--- | :---: | :---: | :--- |
  | `voice` | `str` | Yes | | Tệp âm thanh WAV/MP3 nguồn dạng base64 |
  | `title` | `str` | Yes | | Tên gọi của giọng nói mẫu |
  | `desciption` | `str` | No | `null` | Mô tả giọng nói (lưu ý viết đúng `desciption`) |

### 7. Xóa Giọng Custom
* **URL:** `/api/tts/voices/remove`
* **Method:** `POST`
* **Request Body (JSON):**
  ```json
  {
    "voice_id": 2
  }
  ```

---

## 🐍 Mã Nguồn Mẫu (Python Client)

Dưới đây là một ví dụ Python đơn giản để tương tác với các API của Flow Kit bao gồm cả tính năng TTS:

```python
import time
import requests

BASE_URL = "http://127.0.0.1:8100"

# 1. Cấu hình URL OmniVoice trên Colab
tts_config = requests.put(
    f"{BASE_URL}/api/tts/config",
    json={"base_url": "https://my-colab-tunnel.ngrok-free.app"}
).json()
print(f"-> Đã cấu hình OmniVoice URL: {tts_config['base_url']}")

# 2. Tạo giọng nói từ văn bản
tts_res = requests.post(
    f"{BASE_URL}/api/tts/synthesize",
    json={
        "text": "Chào mừng bạn đến với thế giới của trí tuệ nhân tạo.",
        "voice_id": 0,
        "speed": 1.0
    }
).json()

if tts_res.get("status") == "success":
    print("-> Tổng hợp giọng nói thành công!")
    # tts_res["audio"] chứa base64 của file WAV kết quả
```
