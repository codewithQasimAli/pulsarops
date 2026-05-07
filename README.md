# PulsarOps

> Cloud-native microservices delivery platform — Kubernetes · GitOps · Multi-cloud · Full observability

![CI](https://github.com/codewithQasimAli/pulsarops/actions/workflows/ci.yml/badge.svg)

## Overview

PulsarOps is a production-grade DevSecOps platform that continuously monitors infrastructure endpoint health across three decoupled microservices, deploys via GitOps on Kubernetes, and provides full observability through Prometheus, Grafana, and the ELK Stack.

## Tech Stack

| Layer | Technologies |
|-------|-------------|
| Application | Python 3.11, FastAPI, asyncpg, Redis, PostgreSQL |
| Containers | Docker (multi-stage), Docker Compose |
| Orchestration | Kubernetes (k3s), Helm, ArgoCD |
| CI/CD | GitHub Actions, Jenkins, GitLab CI |
| Infrastructure | Terraform, Ansible, AWS, Azure |
| Observability | Prometheus, Grafana, ELK Stack |
| Security | Bandit SAST, Trivy, non-root containers, K8s RBAC |

## Quick Start

```bash
git clone https://github.com/codewithQasimAli/pulsarops.git
cd pulsarops
cp .env.example .env
make up
```

Services available at:
- API Gateway: http://localhost:8010/docs
- Monitor Service: http://localhost:8011/docs  
- Alert Service: http://localhost:8012/docs
- Prometheus: http://localhost:9091
- Grafana: http://localhost:3001

## Architecture

Three FastAPI microservices behind an API gateway with Redis caching, PostgreSQL persistence, and automated alerting. Deployed on Kubernetes via ArgoCD GitOps. Infrastructure provisioned by Terraform and configured by Ansible across AWS and Azure.

## Services

- **api-gateway** — Redis cache-aside, rate limiting, request routing, Prometheus metrics
- **monitor-service** — Async endpoint polling every 30s, PostgreSQL persistence, health history
- **alert-service** — Automated alert evaluation, Redis state management, webhook support
