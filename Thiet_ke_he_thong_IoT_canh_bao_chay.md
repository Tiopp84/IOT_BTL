# Thiết kế hệ thống IoT cảnh báo cháy ba mức

Cập nhật ngày 11/11/2026 theo backend, Docker Compose và dashboard trong `Src`. Hệ thống là nguyên mẫu giám sát cho bài tập lớn. ESP32 gửi số đo môi trường và tín hiệu hồng ngoại. Backend chạy LightGBM với 13 đặc trưng động, hợp nhất kết quả với tín hiệu Flame IR và trả mức cảnh báo qua MQTT. Dashboard nhận cùng trạng thái qua HTTP.

## Kiến trúc và triển khai

```text
SHT31 + PMS5003 + Flame IR → ESP32 → Mosquitto → FastAPI → LightGBM + luật cảnh báo
                             ESP32 ← Mosquitto ← FastAPI → Nginx → Dashboard
```

| Thành phần | Vai trò trong bản hiện tại |
|---|---|
| ESP32 | Đọc cảm biến, gửi telemetry ở nhịp danh định 1 Hz và nhận kết quả trên topic cảnh báo của node. |
| Mosquitto | Làm trung gian MQTT cho hai chiều telemetry và cảnh báo. Cổng mặc định trên máy chủ là 1883. Cấu hình cho phép kết nối ẩn danh trong mạng lab. |
| FastAPI | Kiểm tra payload, quản lý bộ đệm theo node, suy luận và áp dụng luật cảnh báo. API dùng cổng 8000 trong mạng Docker. |
| Nginx | Phục vụ giao diện tĩnh và chuyển tiếp `/api/` đến FastAPI, tắt buffering để chuyển ngay các sự kiện SSE. Cổng dashboard mặc định trên máy chủ là 8080. |
| Trình duyệt | Hiển thị số đo, 13 đặc trưng, điểm mô hình, cửa sổ bỏ phiếu và nhật ký. Dashboard dùng SSE cho cập nhật trực tiếp. |

Ba dịch vụ máy chủ chạy bằng Docker Compose. Từ `Src`, dùng `docker compose up -d --build --wait` hoặc `make up`. ESP32 dùng IP LAN của máy chạy Docker và cổng MQTT đã cấu hình. Dashboard mở tại [localhost:8080](http://localhost:8080). Có thể đổi cổng ngoài bằng `MQTT_PORT` và `DASHBOARD_PORT`. [Docker Compose](docker-compose.yml) · [Cấu hình Nginx](nginx.conf) · [Hướng dẫn chạy](README.md)

## Cảm biến và phần cứng

| Phần cứng | Dữ liệu hoặc chức năng |
|---|---|
| SHT31 | Nhiệt độ °C và độ ẩm tương đối %RH qua I²C. |
| PMS5003 | PM1.0 và PM2.5 theo µg/m³ qua UART. |
| Flame IR | ESP32 gửi cờ hoạt động, thời gian hoạt động liên tục và giá trị ADC tức thời `ir_raw` nếu firmware cung cấp. |
| LED và còi | Thiết bị có thể ánh xạ `SAFE` sang LED xanh, `ATTENTION` sang LED vàng và `FIRE_ALERT` sang LED đỏ cùng còi. |

Sơ đồ nguyên mẫu WROOM-32 trước đó dùng GPIO21/22 cho I²C, GPIO16 cho UART RX từ PMS5003, GPIO18/19/23 cho ba LED, GPIO25 cho còi và GPIO27 cho tín hiệu số Flame IR. Nguồn thiết kế cho SHT31 là 3,3 V, PMS5003 dùng 5 V và các module dùng GND chung. Đây là bố trí tham chiếu từ thiết kế phần cứng. Backend chỉ tiếp nhận trường `ir_raw` tùy chọn và không quy định chân ADC. Cách nối dây, cực tính Flame IR, chân ADC và nhịp còi cần đối chiếu với firmware và board thực tế.

## Hợp đồng MQTT

| Topic | Chiều | Điều kiện |
|---|---|---|
| `fire/<device_id>/telemetry` | ESP32 → backend | Backend đăng ký `fire/+/telemetry` với QoS 1, bỏ bản tin retain và payload trên 4096 byte. |
| `fire/<device_id>/alert` | Backend → ESP32 | Publish QoS 1, không retain. Vòng phát kiểm tra mỗi giây, gửi khi trạng thái thay đổi hoặc gửi lại sau 5 giây làm heartbeat. |

Payload telemetry có `schema_version: 1`, `device_id`, `boot_id`, `seq`, `uptime_ms`, `sensor_status`, `ir_active` và `ir_active_duration_ms`. Khi `sensor_status` là `OK`, payload cần đủ `temperature_c`, `humidity_pct`, `pm1_ug_m3` và `pm25_ug_m3`. Trường `ir_raw` là tùy chọn. `device_id` phải trùng với topic. Trong mỗi phiên khởi động, `seq` và `uptime_ms` phải tăng. [Ví dụ JSON telemetry](README.md#telemetry-từ-esp32)

Ví dụ bản tin gửi xuống `fire/node01/alert`:

```json
{
  "schema_version": 1,
  "device_id": "node01",
  "health": "OK",
  "risk_level": "FIRE_ALERT",
  "p_fire": 0.82,
  "threshold": 0.70,
  "window": 5,
  "votes": 3,
  "ir_confirmed": false,
  "latched": true,
  "reason": "LightGBM đủ phiếu cảnh báo",
  "timestamp": 1794383496.37
}
```

`timestamp` là thời điểm backend nhận mẫu, theo Unix epoch tính bằng giây. Bản tin heartbeat có thể mang cùng timestamp với bản tin trước. Khi offline, backend xuất `p_fire: null` và chỉ giữ `risk_level: FIRE_ALERT` nếu cảnh báo đã chốt. Firmware chịu trách nhiệm điều khiển còi, theo dõi timeout và kiểm tra gói nhận trên topic `alert`. Mã firmware và kết quả đo phần cứng cần được dẫn riêng khi mô tả hành vi thực tế. [Backend MQTT](backend/app.py) · [Xử lý telemetry](backend/monitor.py)

## Luồng giám sát và dashboard

1. Backend kiểm tra bản tin và bỏ các mẫu trùng, sai thứ tự hoặc thuộc phiên khởi động cũ còn lưu trong bộ nhớ.
2. Với mỗi mẫu hợp lệ, backend tính đặc trưng và điểm `p_fire`. Luật cảnh báo dùng cửa sổ 5 điểm cùng tín hiệu Flame IR. [Chi tiết mô hình và luật](thi_t_k_t_ng_quan_h_th_ng_iot_lightgbm.md)
3. Dashboard mở `GET /api/events` để nhận cấu hình và trạng thái thay đổi. Công cụ kiểm tra có thể đọc ảnh chụp trạng thái bằng `GET /api/status`. Lịch sử giữ tối đa 120 mẫu và 30 sự kiện mỗi node trong RAM. Biểu đồ hiện phần dữ liệu trong 30 giây tính đến mẫu cuối.
4. Backend đánh dấu `OFFLINE` khi mất MQTT hoặc 5 giây không có mẫu mới. Cảnh báo đã chốt vẫn hiện trên dashboard. Backend giữ chốt qua lỗi cảm biến và đổi `boot_id`, còn khởi động lại tiến trình sẽ xóa trạng thái trong RAM. Bản triển khai chưa có giao thức reset hoặc đồng bộ chốt với firmware.
5. Hai kịch bản **Khói đột ngột** và **Nhiệt tăng nhanh** tạo một mẫu mỗi giây qua `GET /api/simulation/events`. Mỗi lượt có 12 giây nền trước sự kiện rồi đến pha hồi phục. Mỗi luồng SSE chạy trên bộ đệm riêng và chỉ gửi kết quả đến trình duyệt.

Để theo dõi bản tin xuống thiết bị trong phiên thử nghiệm, dùng `make alerts`. Một lượt kiểm tra đầu cuối cần đối chiếu telemetry, trạng thái trên dashboard, bản tin `alert` và đầu ra LED/còi trên board. Phiên giả lập kiểm tra luồng suy luận và giao diện. [Makefile](Makefile) · [Lịch sử phát triển và đánh giá](Lich_su_phat_trien_va_huan_luyen.md)
