# Makefile cho stack Docker Compose của Fire Monitor.
# Dùng: make help

COMPOSE      ?= docker compose
DC           := $(COMPOSE) -f docker-compose.yml

# Cổng lấy từ file .env (nếu có) hoặc truyền trực tiếp:
#   make up DASHBOARD_PORT=8081 MQTT_PORT=1884
SERVICE ?=

# Git: branch đích khi push, số commit khi xem log, nội dung commit
GIT    ?= git
BRANCH ?= $(shell $(GIT) rev-parse --abbrev-ref HEAD)
N      ?= 15
MSG    ?=
FILES  ?= .

.DEFAULT_GOAL := help

.PHONY: help build up down restart ps logs logs-backend logs-dashboard sh-backend \
        health wait rebuild clean prune test-mqtt alerts \
        git-status git-log git-diff git-add git-commit git-unstage git-push \
        git-pull git-sync

help: ## Hiển thị danh sách lệnh
	@printf "Fire Monitor - lệnh Docker Compose và Git\n\n"
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_-]+:.*## / {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

build: ## Build image backend và dashboard
	$(DC) build

up: ## Khởi động stack (build nếu cần) và chờ healthy
	$(DC) up -d --build --wait
	@echo "Dashboard: http://localhost:$${DASHBOARD_PORT:-8080}"
	@echo "MQTT:      localhost:$${MQTT_PORT:-1883}"

down: ## Dừng và xoá container
	$(DC) down

restart: ## Khởi động lại toàn bộ stack
	$(DC) restart

ps: ## Xem trạng thái container
	$(DC) ps

logs: ## Xem log (SERVICE=backend|dashboard|mosquitto, mặc định tất cả)
	$(DC) logs -f --tail=200 $(SERVICE)

logs-backend: ## Xem log backend
	$(DC) logs -f --tail=200 backend

logs-dashboard: ## Xem log dashboard
	$(DC) logs -f --tail=200 dashboard

sh-backend: ## Mở shell trong container backend
	$(DC) exec backend /bin/sh

health: ## Kiểm tra API /api/status qua dashboard nginx
	@curl -fsS http://localhost:$${DASHBOARD_PORT:-8080}/api/status >/dev/null \
		&& echo "OK: backend phản hồi" \
		|| (echo "LỖI: backend chưa sẵn sàng" && exit 1)

wait: ## Chờ các service healthy
	$(DC) up -d --wait

rebuild: ## Build lại không dùng cache rồi khởi động
	$(DC) build --no-cache
	$(DC) up -d --wait

test-mqtt: ## Gửi 1 bản tin telemetry mẫu lên broker
	$(DC) exec mosquitto mosquitto_pub -t fire/node01/telemetry -q 1 \
		-m '{"schema_version":1,"device_id":"node01","boot_id":"make-test","seq":1,"uptime_ms":1000,"temperature_c":29.5,"humidity_pct":58.4,"pm1_ug_m3":15.2,"pm25_ug_m3":22.0,"ir_active":false,"ir_active_duration_ms":0,"ir_raw":3548,"sensor_status":"OK"}'

alerts: ## Theo dõi cảnh báo model gửi xuống thiết bị
	$(DC) exec mosquitto mosquitto_sub -t 'fire/+/alert' -v

clean: ## Dừng stack và xoá container/network của project
	$(DC) down --remove-orphans

prune: ## Dọn sâu: dừng stack, xoá container/network và image local của project
	$(DC) down --remove-orphans --rmi local

# ---------- Git ----------

git-status: ## Xem trạng thái thay đổi
	$(GIT) status --short --branch

git-log: ## Xem N commit gần nhất (N=15)
	$(GIT) log --oneline --graph --decorate -n $(N)

git-diff: ## Xem diff chưa stage (FILES=đường/dẫn)
	$(GIT) diff -- $(FILES)

git-add: ## Stage thay đổi (FILES=..., mặc định tất cả)
	$(GIT) add -- $(FILES)
	@$(GIT) status --short

git-commit: ## Commit phần đã stage (MSG="nội dung")
	@test -n "$(MSG)" || { echo 'Thiếu MSG. Dùng: make git-commit MSG="nội dung"'; exit 1; }
	$(GIT) commit -m "$(MSG)"

git-unstage: ## Bỏ stage toàn bộ thay đổi
	$(GIT) reset

git-push: ## Push branch hiện tại lên origin
	$(GIT) push -u origin HEAD

git-pull: ## Kéo code mới bằng rebase + autostash
	$(GIT) pull --rebase --autostash origin $(BRANCH)

git-sync: ## Add tất cả, commit nếu có thay đổi rồi push (MSG="...")
	@$(GIT) add -A
	@if $(GIT) diff --cached --quiet; then \
		echo "Không có thay đổi mới để commit"; \
	else \
		test -n "$(MSG)" || { echo 'Thiếu MSG. Dùng: make git-sync MSG="nội dung"'; exit 1; }; \
		$(GIT) commit -m "$(MSG)"; \
	fi
	$(GIT) push -u origin HEAD
