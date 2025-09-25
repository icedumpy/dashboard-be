# ====== CONFIG ======
# Image & stack
IMAGE ?= fitesa-backend
TAG   ?= 1.0.0
STACK ?= qc
SERVICE ?= api                 # service name in docker-stack.yml (becomes $(STACK)_$(SERVICE))

# Files
STACK_FILE ?= docker-stack.yml
TAR_FILE   ?= /tmp/$(IMAGE)-$(TAG).tar

# Swarm nodes & ssh user (edit these)
NODES ?= node1 node2
SSH_USER ?= $(USER)

# Optional: published port for templating or env passing
APP_PORT ?= 8080

# ====== HELP ======
.PHONY: help
help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "Core:"
	@echo "  make build           Build local image $(IMAGE):$(TAG)"
	@echo "  make save            Save image to $(TAR_FILE)"
	@echo "  make ship            Copy tar to nodes & docker load"
	@echo "  make status          Check status of stack"
	@echo "  make deploy          docker stack deploy -c $(STACK_FILE) $(STACK)"
	@echo "  make redeploy        Build -> save -> ship -> deploy"
	@echo ""
	@echo "Ops:"
	@echo "  make ps              List tasks"
	@echo "  make logs            Follow service logs"
	@echo "  make update          Service update to new TAG (requires TAG=...)"
	@echo "  make rollback        Roll back last update"
	@echo "  make rm              Remove stack"
	@echo ""
	@echo "Swarm setup:"
	@echo "  make swarm-init      docker swarm init (on manager)"
	@echo "  make label           Label current node with qc_role=api"
	@echo ""
	@echo "Vars (override like VAR=value make target):"
	@echo "  IMAGE, TAG, STACK, SERVICE, NODES, SSH_USER, STACK_FILE, APP_PORT"

# ====== CORE PIPELINE ======
.PHONY: build
build:
	docker build -t $(IMAGE):$(TAG) ./api

.PHONY: save
save:
	docker image inspect $(IMAGE):$(TAG) >/dev/null 2>&1 || (echo "Image not found. Run 'make build' first."; exit 1)
	docker save $(IMAGE):$(TAG) -o $(TAR_FILE)
	@echo "Saved -> $(TAR_FILE)"

.PHONY: status
status:
	@echo "🚨 Stack 🚨"
	docker stack ls
	@echo "🚨 Service 🚨"
	docker service ls
	@echo "Saved -> $(TAR_FILE)"

.PHONY: ship
ship: save
	@for n in $(NODES); do \
		echo ">> Ship to $$n"; \
		scp $(TAR_FILE) $(SSH_USER)@$$n:$(TAR_FILE); \
		ssh $(SSH_USER)@$$n 'docker load -i $(TAR_FILE) && rm -f $(TAR_FILE)'; \
	done
	@rm -f $(TAR_FILE)
	@echo "All nodes ready."

.PHONY: deploy
deploy:
	APP_PORT=$(APP_PORT) docker stack deploy -c $(STACK_FILE) $(STACK)
	@echo "Deployed stack $(STACK)."

.PHONY: redeploy
redeploy: build ship deploy

# ====== OPS ======
.PHONY: ps
ps:
	docker stack ps $(STACK)

.PHONY: logs
logs:
	docker service logs -f $(STACK)_$(SERVICE)

.PHONY: update
update:
	@[ -n "$(TAG)" ] || (echo "Set TAG=<new-tag>"; exit 1)
	docker service update --image $(IMAGE):$(TAG) --update-order start-first --rollback-parallelism 1 $(STACK)_$(SERVICE)

.PHONY: rollback
rollback:
	docker service rollback $(STACK)_$(SERVICE)

.PHONY: rm
rm:
	docker stack rm $(STACK)

# ====== SWARM UTIL ======
.PHONY: swarm-init
swarm-init:
	docker swarm init || true

.PHONY: label
label:
	@node_name=$$(docker node ls --format '{{.Hostname}}' | head -n1); \
	echo "Label node $$node_name: qc_role=api"; \
	docker node update --label-add qc_role=api $$node_name


clean:
	find . -type d -name "__pycache__" -exec rm -r {} +
	find . -type f -name "*.pyc" -delete

dev:
	docker compose up