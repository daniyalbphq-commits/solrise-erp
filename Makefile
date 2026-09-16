# Solrise ERP - single entry point for every environment.
# Every target sources .env, so the same commands work on CachyOS + any VPS.
SHELL := /bin/bash
ENV_FILE := .env
LOCAL := compose/compose.local.yaml
PROD := compose/compose.prod.yaml
# Production topology with MariaDB on RDS (no embedded db container).
AWS := compose/compose.aws.yaml

COMPOSE_CMD ?= podman-compose

# The services that run CUSTOM_IMAGE:CUSTOM_TAG. A re-pushed image keeps its tag -
# only the digest changes - and podman-compose compares the *service configuration*,
# not the digest, so a plain `up -d` leaves the old containers running and the new
# image silently unused. Recreating these is what makes a CI push go live.
# redis and Traefik are deliberately left alone, so a deploy does not drop the
# cache, the queue or the TLS listener.
APP_SERVICES := backend websocket queue-short queue-long scheduler frontend

-include $(ENV_FILE)
export

.PHONY: help image local-up local-down init site logs ps shell \
        prod-up prod-down prod-logs aws-up aws-down aws-logs aws-rollout \
        backup restore fixtures pull-fixtures media verify branding app-fixtures \
        clear-cache

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

image: ## Build the custom erpnext+hrms image
	./scripts/build-image.sh

local-up: ## Start the local stack and roll out a rebuilt image
	$(COMPOSE_CMD) -f $(LOCAL) --env-file $(ENV_FILE) up -d
	$(COMPOSE_CMD) -f $(LOCAL) --env-file $(ENV_FILE) up -d --force-recreate $(APP_SERVICES)
	SITE_ENV=local ./scripts/clear-cache.sh

local-down: ## Stop the local stack (keeps volumes)
	$(COMPOSE_CMD) -f $(LOCAL) --env-file $(ENV_FILE) down

site: ## Create the site and install apps (idempotent)
	./scripts/create-site.sh

media: ## Point file storage at the S3 media bucket (S3_MEDIA_* in .env)
	./scripts/setup-media.sh

verify: ## Check the deployed application layer (branding, assistant, chat, RBAC)
	./scripts/run-python.sh scripts/verify_app_layer.py

verify-chat: ## Have a conversation with the deployed chat (menu, intent, gates, audit)
	./scripts/run-python.sh scripts/verify_chat.py

branding: ## White-label branding without the app (the DocType half of docs/10)
	./scripts/run-python.sh scripts/branding_only.py

app-fixtures: ## Import app fixtures that need no app (workflows, notifications, reports, dashboards)
	./scripts/import_app_fixtures.sh

init: image local-up site ## Full local bring-up in one shot

logs: ## Tail all local container logs
	$(COMPOSE_CMD) -f $(LOCAL) --env-file $(ENV_FILE) logs -f --tail=100

ps: ## Show local container status
	$(COMPOSE_CMD) -f $(LOCAL) --env-file $(ENV_FILE) ps

shell: ## Open a bash shell in the backend container
	$(CONTAINER_ENGINE) exec -it solrise-backend bash

prod-up: ## Start the production stack and roll out a re-pushed image
	$(COMPOSE_CMD) -f $(PROD) --env-file $(ENV_FILE) up -d
	$(COMPOSE_CMD) -f $(PROD) --env-file $(ENV_FILE) up -d --force-recreate $(APP_SERVICES)
	SITE_ENV=prod ./scripts/clear-cache.sh

prod-down: ## Stop the production stack
	$(COMPOSE_CMD) -f $(PROD) --env-file $(ENV_FILE) down

prod-logs: ## Tail production logs
	$(COMPOSE_CMD) -f $(PROD) --env-file $(ENV_FILE) logs -f --tail=100

aws-up: ## Start the AWS stack (EC2 + RDS) and roll out a re-pushed image
	$(COMPOSE_CMD) -f $(AWS) --env-file $(ENV_FILE) up -d
	$(COMPOSE_CMD) -f $(AWS) --env-file $(ENV_FILE) up -d --force-recreate $(APP_SERVICES)
	SITE_ENV=aws ./scripts/clear-cache.sh

aws-rollout: ## Recreate only the app containers (pick up a re-pushed tag)
	$(COMPOSE_CMD) -f $(AWS) --env-file $(ENV_FILE) up -d --force-recreate $(APP_SERVICES)
	SITE_ENV=aws ./scripts/clear-cache.sh

clear-cache: ## Clear the site + asset caches (SITE_ENV selects the stack)
	./scripts/clear-cache.sh

aws-down: ## Stop the AWS stack
	$(COMPOSE_CMD) -f $(AWS) --env-file $(ENV_FILE) down

aws-logs: ## Tail AWS stack logs
	$(COMPOSE_CMD) -f $(AWS) --env-file $(ENV_FILE) logs -f --tail=100

backup: ## Dump DB + files and copy them to $(BACKUP_DIR)
	./scripts/backup.sh

restore: ## Restore from $(BACKUP_DIR) (see scripts/restore.sh usage)
	./scripts/restore.sh

fixtures: ## Export fixtures inside the container and pull them into ./fixtures
	./scripts/export-fixtures.sh
	./scripts/pull-fixtures.sh

pull-fixtures: ## Pull already-exported fixtures out of the container
	./scripts/pull-fixtures.sh
