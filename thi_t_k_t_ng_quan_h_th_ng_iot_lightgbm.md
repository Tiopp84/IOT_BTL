# THIẾT KẾ TỔNG QUAN HỆ THỐNG IOT CẢNH BÁO CHÁY 3 MỨC

### (Hợp nhất dữ liệu đa cảm biến với LightGBM & MQTT Mosquitto)

**Phiên bản:** 2.0  
**Ngày cập nhật:** 10/11/2026  
**Kiến trúc chính:** ESP32 + SHT31 + PMS5003 + Flame IR $\rightarrow$ Mosquitto MQTT Broker $\rightarrow$ Python Backend (LightGBM + Decision Engine) $\rightarrow$ ESP32 Actuators  

---

## 1. PHẠM VI VÀ QUYẾT ĐỊNH THIẾT KẾ

1. **Phần cứng thu thập dữ liệu (End-Node):**
   * **ESP32 DevKit WROOM-32** đọc dữ liệu từ **4 giá trị cảm biến môi trường thô**: Nhiệt độ ($T$) và Độ ẩm ($H$) từ SHT31, Bụi mịn PM1.0 và PM2.5 từ PMS5003.
   * **Cảm biến Hồng ngoại Flame IR** phát hiện ngọn lửa trực tiếp (tín hiệu số DO), không đưa vào mô hình LightGBM mà tham gia hợp nhất ở Tầng Quyết định (Decision Engine).

2. **Mô hình Trí tuệ Nhân tạo (Machine Learning):**
   * Sử dụng thuật toán **LightGBM** (hoặc `HistGradientBoostingClassifier`) huấn luyện trên 16 đặc trưng chuỗi thời gian (bao gồm 4 đặc trưng thô, 4 tốc độ biến thiên $\Delta_5$, 6 thống kê trượt Rolling Mean/Std, và 2 tỷ lệ biến).
   * File mô hình được đóng gói thành `lightgbm_smoke_model.joblib` và `metadata.json` chạy trực tiếp trên Python Backend Server.

3. **Truyền thông & Giao thức:**
   * Gửi dữ liệu định kỳ $1\text{ Hz}$ qua **MQTT Broker (Mosquitto)** cài đặt trên Laptop/Server cục bộ.

4. **Phân cấp Cảnh báo (3 Mức):**
   * `SAFE` (An toàn): LED Xanh sáng, còi tắt.
   * `ATTENTION` (Cần chú ý): LED Vàng sáng, còi tắt.
   * `FIRE_ALERT` (Cảnh báo cháy): LED Đỏ sáng, còi báo động theo nhịp. Kích hoạt cơ chế Chốt cảnh báo (Alarm Latching).

---

## 2. KIẾN TRÚC TỔNG THỂ HỆ THỐNG

```
+-----------------------------------------------------------------------------------+
|                           ESP32 End-Node (Thu thập & Điều khiển)                 |
|  +--------------------+    +--------------------+    +-------------------------+  |
|  | SHT31 (I2C)        |    | PMS5003 (UART)     |    | Flame IR (GPIO DO)      |  |
|  | Nhiệt độ & Độ ẩm   |    | PM1.0 & PM2.5      |    | Tín hiệu ngọn lửa       |  |
|  +---------+----------+    +---------+----------+    +------------+------------+  |
+------------|-------------------------|----------------------------|---------------+
             | (1Hz Telemetry)         |                            |
             +-------------------------+                            |
                                       |                            |
                                       v                            v
                      +--------------------------------------------------+
                      |         MQTT Broker (Mosquitto Port: 1883)       |
                      +------------------------+-------------------------+
                                               |
                                               v
+-----------------------------------------------------------------------------------+
|                        Python Backend Service (Laptop / Server)                   |
|  +-----------------------------------------------------------------------------+  |
|  | 1. Circular Buffer (W = 5 mẫu)                                              |  |
|  +-------------------------------------+---------------------------------------+  |
|                                        |                                          |
|                                        v                                          |
|  +-----------------------------------------------------------------------------+  |
|  | 2. Online Feature Extractor (16 đặc trưng chuỗi thời gian)                  |  |
|  +-------------------------------------+---------------------------------------+  |
|                                        |                                          |
|                                        v                                          |
|  +-----------------------------------------------------------------------------+  |
|  | 3. LightGBM Model Inference (Tính p_fire trong khoảng [0, 1])               |  |
|  +-------------------------------------+---------------------------------------+  |
|                                        |                                          |
|                                        v                                          |
|  +-----------------------------------------------------------------------------+  |
|  | 4. Sliding Window Voting (Xác nhận >= 3/5 mẫu p_fire >= T_HIGH)              |  |
|  +-------------------------------------+---------------------------------------+  |
|                                        |                                          |
|                                        v                                          |
|  +-----------------------------------------------------------------------------+  |
|  | 5. Decision Engine (+ Tín hiệu Flame IR -> SAFE / ATTENTION / FIRE_ALERT)   |  |
|  +-------------------------------------+---------------------------------------+  |
+----------------------------------------|------------------------------------------+
                                         | (MQTT Publish Decision)
                                         v
+-----------------------------------------------------------------------------------+
|                         ESP32 Actuators & Alarm Control                           |
|  - LED Green (SAFE)  |  LED Yellow (ATTENTION)  |  LED Red & Buzzer (FIRE_ALERT)  |
+-----------------------------------------------------------------------------------+
```

---

## 3. THIẾT KẾ PHẦN CỨNG VÀ KẾT NỐI

### 3.1. Bảng sơ đồ nối dây ESP32 DevKit WROOM-32

| Thiết bị | Chân thiết bị | Chân kết nối ESP32 | Ghi chú kỹ thuật |
| :--- | :--- | :--- | :--- |
| **SHT31** | VCC, GND | 3V3, GND | Nguồn 3.3V |
| | SDA, SCL | GPIO21, GPIO22 | Giao tiếp I2C chuẩn |
| **PMS5003** | VCC, GND | **5V (VIN)**, GND | **Quạt PMS5003 bắt buộc cấp 5V** |
| | TXD, RXD | GPIO16 (RX1), Để trống | Logic UART 3.3V |
| **Flame IR** | VCC, GND | 3V3, GND | Nguồn 3.3V |
| | DO | GPIO27 | Digital Input (Active-Low / Active-High) |
| **LEDs** | Green, Yellow, Red | GPIO18, GPIO19, GPIO23 | Nối qua điện trở $330\,\Omega$ xuống GND |
| **Buzzer** | VCC, GND, IN | 3V3, GND, GPIO25 | Module còi chủ động (Active Buzzer) |
| **Nút Mute** | Pin 1, Pin 2 | GPIO26, GND | Chế độ `INPUT_PULLUP` |

---

## 4. QUY TRÌNH HUẤN LUYỆN MÔ HÌNH LIGHTGBM (OFFLINE PIPELINE)

### 4.1. Tập dữ liệu & Tiền xử lý chống Rò rỉ Dữ liệu (Data Leakage)

1. **Nguồn dữ liệu:** `smoke_detection_iot.csv` (Kaggle).
2. **Phân tách phiên đo (Session Detection):** Phân tách 5 phiên thử nghiệm môi trường bằng khoảng gián đoạn $\Delta\text{UTC} > 10\text{ giây}$.
3. **Chiến lược chia dữ liệu (Blocked Time-Series Split):**
   * **Train Set (60%):** Lấy đoạn đầu của từng phiên.
   * **Validation Set (20%):** Lấy đoạn giữa để tối ưu ngưỡng $T_{\text{LOW}}, T_{\text{HIGH}}$.
   * **Test Set (20%):** Lấy đoạn cuối để đánh giá độc lập.

### 4.2. Danh sách 16 Đặc trưng Chuỗi thời gian (Feature Map)

$$\mathbf{X}_t = \big[ T, H, PM_{1.0}, PM_{2.5}, PM_{\text{ratio}}, TH_{\text{ratio}}, \Delta T_5, \Delta H_5, \Delta PM1_5, \Delta PM2.5_5, \bar{T}_5, \bar{H}_5, \bar{PM1}_5, \bar{PM2.5}_5, \sigma(T)_5, \sigma(H)_5 \big]$$

Trong đó:
* $\Delta X_5 = X_t - X_{t-5}$: Tốc độ biến thiên trong 5 mẫu ($\sim 5\text{ giây}$).
* $\bar{X}_5$: Trung bình trượt (Rolling Mean) 5 mẫu.
* $\sigma(X)_5$: Độ lệch chuẩn trượt (Rolling Standard Deviation) 5 mẫu.
* $PM_{\text{ratio}} = \frac{PM2.5 + 1e-3}{PM1.0 + 1e-3}$.

### 4.3. Kết quả Kiểm thử & So sánh Hiệu năng

| Chỉ số Đánh giá | Random Forest v1 (Baseline - 4 biến thô) | **LightGBM Tối ưu (Đề xuất - 16 biến)** |
| :--- | :---: | :---: |
| **F1-Score** | 0.7621 | **0.9426** |
| **Precision** | 0.6980 | **0.8977** |
| **Recall (Phát hiện cháy)** | 0.8391 | **0.9922** (Bỏ sót cháy $< 0.8\%$) |
| **ROC AUC** | 0.7292 | **0.9981** |
| **Dung lượng Model** | $> 15\text{ MB}$ | **$< 150\text{ KB}$** |
| **Thời gian Suy luận** | $\sim 12\text{ ms}$ | **$< 0.5\text{ ms}$** |

---

## 5. GIAO THỨC MQTT & CẤU TRÚC DỮ LIỆU

### 5.1. Danh sách Topics MQTT

* `fire/node01/telemetry` (ESP32 $\rightarrow$ Backend, QoS 1): Gửi dữ liệu cảm biến thô.
* `fire/node01/decision` (Backend $\rightarrow$ ESP32, QoS 1): Gửi kết quả quyết định hiển thị.
* `fire/node01/command` (Backend $\rightarrow$ ESP32, QoS 1): Gửi lệnh điều khiển (Reset / Mute).
* `fire/node01/ack` (ESP32 $\rightarrow$ Backend, QoS 1): Xác nhận xử lý lệnh thành công.

### 5.2. Format JSON Chuẩn

#### Telemetry Payload (Gửi mỗi 1 giây):
```json
{
  "schema_version": 1,
  "device_id": "node01",
  "boot_id": "b8a1-9f3c",
  "seq": 1042,
  "uptime_ms": 1042000,
  "temperature_c": 29.5,
  "humidity_pct": 58.4,
  "pm1_ug_m3": 15.2,
  "pm25_ug_m3": 22.0,
  "ir_active": false,
  "ir_active_duration_ms": 0,
  "sensor_status": "OK"
}
```

#### Decision Payload (Trả về phản hồi):
```json
{
  "schema_version": 1,
  "device_id": "node01",
  "boot_id": "b8a1-9f3c",
  "source_seq": 1042,
  "p_fire": 0.8925,
  "risk_level": "FIRE_ALERT",
  "alarm_latched": true,
  "health": "OK",
  "valid_for_ms": 5000
}
```

---

## 6. TẦNG QUYẾT ĐỊNH (DECISION ENGINE) VÀ CƠ CHẾ THỜI GIAN REAL-TIME

### 6.1. Quy tắc Xác nhận Cửa sổ Thời gian (Sliding Window Voting)
1. **Hàng đợi đệm (Circular Buffer):** Giữ $W = 5$ mẫu gần nhất nhận từ MQTT.
2. **Xác nhận Điểm cao (`high_confirmed`):**
   $$\text{high\_confirmed} = \left( \sum_{i=0}^{4} \mathbb{I}(p\_fire_{t-i} \ge T_{\text{HIGH}}) \right) \ge 3 \quad \text{với } T_{\text{HIGH}} = 0.70$$
3. **Xác nhận Flame IR (`ir_confirmed`):** Tín hiệu IR duy trì trạng thái Active liên tục $\ge 2000\text{ ms}$.

### 6.2. Ma trận Luật Hợp nhất 3 Ngưỡng (Data Fusion Matrix)

```text
Luật 1: IF alarm_latched == True                           ---> FIRE_ALERT (Giữ nguyên Latch)
Luật 2: IF high_confirmed OR (ir_confirmed AND p_fire >= 0.30) ---> FIRE_ALERT (Set Latch = True)
Luật 3: IF p_fire >= 0.30 OR ir_confirmed                  ---> ATTENTION
Luật 4: IF p_fire < 0.30 (10 mẫu liên tiếp) AND IR == False ---> SAFE
```

### 6.3. Trạng thái Hoạt động trên ESP32

| Trạng thái Cảnh báo | LED Xanh (GPIO18) | LED Vàng (GPIO19) | LED Đỏ (GPIO23) | Còi báo (GPIO25) |
| :--- | :---: | :---: | :---: | :---: |
| **`SAFE`** | **BẬT** | TẮT | TẮT | TẮT |
| **`ATTENTION`** | TẮT | **BẬT** | TẮT | TẮT |
| **`FIRE_ALERT`** | TẮT | TẮT | **BẬT** | **Kích hoạt phát nhịp (200ms ON / 200ms OFF)** |
| **`WARMUP` / Mất kết nối** | TẮT | **Nhấp nháy (1Hz)** | TẮT | TẮT |

---

## 7. QUY TRÌNH THỰC NGHIỆM VÀ KIỂM THỬ (END-TO-END TESTING)

1. **Khởi động MQTT Broker:** Chạy Mosquitto Broker trên Laptop local (`mosquitto -v`).
2. **Khởi động Python Backend:** Chạy script `backend/mqtt_service.py` để nạp model và sẵn sàng subscribe telemetry.
3. **Bật nguồn ESP32:** Đèn LED Vàng nhấp nháy trong 5 giây khởi tạo (`WARMUP`). Khi nhận được gói `decision` đầu tiên có `risk_level: SAFE`, LED Xanh chuyển sang sáng ổn định.
4. **Thử nghiệm kịch bản báo động:**
   * **Thử nghiệm Khói/Nhiệt:** Dùng nguồn tạo khói/nhiệt đưa gần SHT31/PMS5003. Quan sát $p\_fire$ tăng dần. Khi 3/5 mẫu đạt $p\_fire \ge 0.70$, hệ thống chuyển sang `FIRE_ALERT`, LED Đỏ sáng và còi hú.
   * **Thử nghiệm Nút Mute:** Nhấn nút trên GPIO26 $\rightarrow$ Còi tắt nhưng LED Đỏ vẫn sáng giữ Latch.
   * **Thử nghiệm Mất mạng (Fault Tolerance):** Ngắt Wi-Fi $\rightarrow$ Sau 5 giây Timeout, ESP32 chuyển sang trạng thái cảnh báo mất kết nối, giữ nguyên trạng thái Latch an toàn nếu trước đó đang có cảnh báo cháy.