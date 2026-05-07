.PHONY: up down build logs test lint clean

up:
	docker-compose up --build -d

down:
	docker-compose down

build:
	docker-compose build

logs:
	docker-compose logs -f

test:
	@echo "=== PulsarOps Health Checks ==="
	@echo "--- api-gateway (port 8010) ---"
	@curl -sf http://localhost:8010/health | python -m json.tool || echo "FAIL: api-gateway unreachable"
	@echo "--- monitor-service (port 8011) ---"
	@curl -sf http://localhost:8011/health | python -m json.tool || echo "FAIL: monitor-service unreachable"
	@echo "--- alert-service (port 8012) ---"
	@curl -sf http://localhost:8012/health | python -m json.tool || echo "FAIL: alert-service unreachable"

lint:
	@docker run --rm -v $(PWD)/services/api-gateway:/code pycqa/flake8 /code --max-line-length=120
	@docker run --rm -v $(PWD)/services/monitor-service:/code pycqa/flake8 /code --max-line-length=120
	@docker run --rm -v $(PWD)/services/alert-service:/code pycqa/flake8 /code --max-line-length=120

clean:
	docker-compose down -v --remove-orphans
