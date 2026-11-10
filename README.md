# Fire Monitor

Dashboard giám sát bài tập lớn IoT. Stack gồm Mosquitto, FastAPI chạy model LightGBM đã train và Nginx phục vụ dashboard. Backend đọc MQTT và gửi trạng thái đến trình duyệt qua SSE.

## Chạy trên máy có Docker Compose

Từ thư mục `BTL/Src`:

```bash
docker compose up -d --build --wait
docker compose ps
```

Mở [dashboard](http://localhost:8080). Từ thiết bị khác trong cùng mạng, dùng địa chỉ IP của máy chạy Docker thay cho `localhost`. ESP32 kết nối MQTT đến IP đó, cổng `1883`. Broker cho phép kết nối ẩn danh để demo trong mạng lab.

Nếu stack cũ trong `BTL/Server` đang dùng cổng 1883, có thể chạy stack mới với cổng khác. Giữ giá trị này cho các lần chạy tiếp theo, ví dụ đặt `MQTT_PORT=1884` trong file `.env` cạnh Compose. Đặt `DASHBOARD_PORT` nếu cần đổi cổng web.

```bash
MQTT_PORT=1884 docker compose up -d --build --wait
```

Trong mục **Nguồn dữ liệu**, chọn **Giả lập 1 Hz · Khói đột ngột** để xem bụi mịn tăng nhanh rồi giảm khi khói tan, hoặc **Giả lập 1 Hz · Nhiệt tăng nhanh** để xem nhiệt độ tăng và độ ẩm tương đối giảm trong khi bụi gần như ổn định. Mỗi kịch bản có 12 giây đo nền trước khi sự kiện xảy ra. LightGBM xử lý từng mẫu trong phiên riêng của trình duyệt. Chọn **Chạy lại** để bắt đầu lại kịch bản hoặc **ESP32 qua MQTT** để xem dữ liệu thiết bị.

## Telemetry từ ESP32

Dashboard hiển thị số đo, 16 đặc trưng đầu vào và cửa sổ điểm từ backend. Khi chưa nhận bản tin hợp lệ, giao diện hiển thị trạng thái chờ. Khi thiết bị ngừng gửi khoảng 5 giây, dashboard báo mất dữ liệu và giữ biểu đồ lịch sử.

Gửi QoS 1 lên `fire/node01/telemetry`, tắt retain. ID trong topic phải khớp payload. Tạo `boot_id` mới mỗi lần ESP32 khởi động và tăng `seq` mỗi mẫu. Gửi mẫu ở 1 Hz.

```json
{
  "schema_version": 1,
  "device_id": "node01",
  "boot_id": "boot-unique-id",
  "seq": 1,
  "uptime_ms": 1000,
  "temperature_c": 29.5,
  "humidity_pct": 58.4,
  "pm1_ug_m3": 15.2,
  "pm25_ug_m3": 22.0,
  "ir_active": false,
  "ir_active_duration_ms": 0,
  "sensor_status": "OK"
}
```

Khi cảm biến lỗi, gửi trạng thái khác `OK`. Backend sẽ bỏ cửa sổ suy luận hiện tại và chờ mẫu mới. Mẫu trùng hoặc đến sai thứ tự bị bỏ qua.

## Chính sách demo

- Backend tạo 16 đặc trưng đúng thứ tự của `smoke_model_bundle/metadata.json` và nạp model một lần khi khởi động. Khoảng gián đoạn lớn hơn 2,5 giây theo uptime, mất số thứ tự, đổi phiên hoặc mất kết nối sẽ bắt đầu lại cửa sổ.
- LightGBM xác nhận cảnh báo khi ít nhất 3 trong đủ 5 điểm đạt 0,95. Flame IR kích hoạt từ 2 giây kết hợp điểm từ 0,30 cũng xác nhận cảnh báo. Một trong hai điều kiện IR hoặc điểm 0,30 tạo mức cần chú ý. Mức an toàn yêu cầu 10 mẫu liên tiếp dưới 0,30 và IR tắt.
- Dashboard giữ tối đa 120 mẫu và 30 sự kiện cho mỗi thiết bị trong RAM. Chốt cảnh báo demo được giữ qua lỗi cảm biến, mất dữ liệu và ESP32 khởi động lại. Khởi động lại backend xóa phiên giám sát và chốt trong RAM. Cảnh báo được hiển thị trên API và dashboard.
- `GET /api/status` trả trạng thái JSON. `GET /api/events` phát trạng thái thay đổi qua SSE.

```bash
docker compose logs -f backend
docker compose down
```
