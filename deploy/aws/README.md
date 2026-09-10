# AWS production deploy (ECS Fargate)

Recommended topology:

```
Internet → CloudFront (optional) → ALB (TLS) → ECS Fargate service
                                      ├─ RDS PostgreSQL (Multi-AZ)
                                      └─ ElastiCache Redis
Secrets ← Secrets Manager / SSM Parameter Store
Logs    → CloudWatch Logs (/ecs/shafsky-backend)
```

## Prerequisites

1. ECR repository `shafsky-backend`
2. RDS Postgres with TLS; run security group allowing ECS tasks
3. ElastiCache Redis (auth token recommended)
4. ALB + target group on port **4000**
   - Health check path: **`/ready`**
   - Healthy = HTTP **200**; unhealthy = **503**
5. Secrets in Secrets Manager (see `ecs-task-definition.json`)
6. IAM: `ecsTaskExecutionRole` (pull image + secrets), task role for app AWS calls if any

## Build & push

```bash
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com
docker build -t shafsky-backend:latest .
docker tag shafsky-backend:latest $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/shafsky-backend:latest
docker push $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/shafsky-backend:latest
```

## Register task & service

1. Edit `ecs-task-definition.json` — replace `ACCOUNT_ID`, `REGION`, secret ARNs, image URI.
2. `aws ecs register-task-definition --cli-input-json file://deploy/aws/ecs-task-definition.json`
3. Create/update ECS service (Fargate, awsvpc, private subnets + ALB target group).

## Required production environment

| Name | Value |
|------|--------|
| `ENVIRONMENT` | `production` |
| `TRUST_PROXY` | `true` |
| `REQUIRE_REDIS` | `true` |
| `ALLOWED_ORIGINS` | Exact HTTPS frontend origin(s) only |
| `RUN_MIGRATIONS` | `true` (or run a one-off migrate task before traffic) |
| `WEB_CONCURRENCY` | `2`+ based on CPU |

## ALB notes

- Terminate TLS on the ALB (ACM certificate).
- Forward `X-Forwarded-For` / `X-Forwarded-Proto` (default).
- Target group health check: `/ready`, matcher `200`.

## Backups

Use **RDS automated backups + PITR**. The in-app `/api/admin/dr/backup` endpoint creates **drill metadata only**, not a database dump.

## Smoke after deploy

```bash
curl -fsS https://api.yourdomain.com/live
curl -fsS https://api.yourdomain.com/ready
curl -fsS https://api.yourdomain.com/api/health
```
