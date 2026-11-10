.PHONY: up down clean logs test status reset service-down disk-full disk-full-hard bad-config pending history approve reject

up:            ## build and start everything (first run pulls the model, ~1 GB)
	docker compose up -d --build

down:
	docker compose down

clean:         ## also delete volumes (agent state, model cache)
	docker compose down -v

logs:          ## follow the agent decisions
	docker compose logs -f --no-log-prefix agent

test: .venv    ## unit + graph tests, no Docker needed
	cd agent && ../.venv/bin/pytest -q

.venv:
	python3 -m venv .venv && .venv/bin/pip install -q -e 'agent[test]'

status reset service-down disk-full disk-full-hard bad-config:
	@scripts/inject.sh $@

pending history:
	@docker compose exec agent python -m sre_agent.cli $@

approve reject: ## make approve ID=<id>
	@docker compose exec agent python -m sre_agent.cli $@ $(ID)
