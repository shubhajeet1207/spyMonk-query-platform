# 🚀 Complete Deployment Roadmap: spyMonk-DB + spyMonk-warehouse

## 📊 Architecture Overview

```
Internet
   │
   ▼
┌─────────────────────────────────────┐
│   Cloudflare Pages (Frontend)       │
│   - React + TypeScript UI           │
│   - Static assets (CDN)             │
└────────────┬────────────────────────┘
             │ HTTPS + API Key
             ▼
┌─────────────────────────────────────┐
│   Backend API (FastAPI)             │
│   Platform: Railway/Render/AWS      │
│   - Authentication middleware       │
│   - Rate limiting                   │
│   - Input validation                │
└────────────┬────────────────────────┘
             │ gRPC + mTLS
             ▼
┌─────────────────────────────────────┐
│   spyMonk-DB Cluster (3 nodes)      │
│   Platform: Railway/DigitalOcean    │
│   - Node 1: Leader election         │
│   - Node 2: Replica                 │
│   - Node 3: Replica                 │
│   - Private network only            │
└─────────────────────────────────────┘
```

---

## 🔐 PHASE 1: SECURITY HARDENING — ✅ COMPLETE

> **Status:** every vulnerability originally listed here has been fixed.
> The table below is kept as a historical record.

| Severity | Issue | Resolution |
|----------|-------|------------|
| 🔴 CRITICAL | SQL Injection | Fixed — SELECT-only allow-list with comment stripping (`auth.py: sanitize_sql_query`) |
| 🔴 CRITICAL | No Authentication | Fixed — `X-API-Key` auth on all data endpoints (`auth.py: verify_api_key`) |
| 🔴 CRITICAL | Open CORS | Fixed — origin allow-list from `CORS_ORIGINS` env |
| 🔴 CRITICAL | No DB Authentication | Fixed — gRPC bearer-token interceptor (`SPYMONK_DB_AUTH_TOKEN`) + optional TLS (`SPYMONK_TLS_CERT`/`SPYMONK_TLS_KEY`) |
| 🟡 HIGH | Unlimited file uploads | Fixed — size, extension, and row-count limits on `/upload` |
| 🟡 HIGH | Error message leaks | Fixed — generic 500s in production; gRPC internal errors no longer echo exception text |

Details: [SECURITY_FIXES_SUMMARY.md](./SECURITY_FIXES_SUMMARY.md).

---

## 📦 PHASE 2: PREPARE PROJECTS FOR DEPLOYMENT

### 2.1 spyMonk-DB Preparation

#### File: `spyMonk-DB/Dockerfile`
```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements_enterprise.txt .
RUN pip install --no-cache-dir -r requirements_enterprise.txt

# Copy source code
COPY spymonk_enterprise/ ./spymonk_enterprise/
COPY setup.py .
COPY README_ENTERPRISE.md .

# Install the package
RUN pip install -e .

# Create data directory
RUN mkdir -p /data

# Expose gRPC port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD python -c "import socket; s = socket.socket(); s.connect(('localhost', 5000)); s.close()" || exit 1

# Run server
CMD ["python", "-m", "spymonk_enterprise.spanserver.server", \
     "--node-id=${NODE_ID}", \
     "--data-dir=/data", \
     "--port=5000", \
     "--peers=${PEERS}"]
```

#### File: `spyMonk-DB/docker-compose.yml`
```yaml
version: '3.8'

services:
  db-node1:
    build: .
    container_name: spymonk-db-node1
    environment:
      - NODE_ID=node1
      - PEERS=db-node2:5000,db-node3:5000
      - AUTH_TOKEN=${DB_AUTH_TOKEN}
    ports:
      - "5000:5000"
    volumes:
      - db-data-1:/data
    networks:
      - spymonk-net
    restart: unless-stopped

  db-node2:
    build: .
    container_name: spymonk-db-node2
    environment:
      - NODE_ID=node2
      - PEERS=db-node1:5000,db-node3:5000
      - AUTH_TOKEN=${DB_AUTH_TOKEN}
    ports:
      - "5001:5000"
    volumes:
      - db-data-2:/data
    networks:
      - spymonk-net
    restart: unless-stopped

  db-node3:
    build: .
    container_name: spymonk-db-node3
    environment:
      - NODE_ID=node3
      - PEERS=db-node1:5000,db-node2:5000
      - AUTH_TOKEN=${DB_AUTH_TOKEN}
    ports:
      - "5002:5000"
    volumes:
      - db-data-3:/data
    networks:
      - spymonk-net
    restart: unless-stopped

volumes:
  db-data-1:
  db-data-2:
  db-data-3:

networks:
  spymonk-net:
    driver: bridge
```

#### File: `spyMonk-DB/.env.example`
```bash
# Authentication token for database cluster
DB_AUTH_TOKEN=your-secure-random-token-here-change-me

# Node configuration
NODE_ID=node1
PEERS=node2:5000,node3:5000

# Monitoring (optional)
PROMETHEUS_ENABLED=true
PROMETHEUS_PORT=9090
```

---

### 2.2 spyMonk-warehouse Backend Preparation

#### File: `spyMonk-warehouse/backend/Dockerfile`
```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY main.py .
COPY auth.py .
COPY config.py .

# Create upload directory
RUN mkdir -p /tmp/uploads

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Run with uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

#### File: `spyMonk-warehouse/backend/requirements.txt`
```txt
fastapi==0.104.1
uvicorn[standard]==0.24.0
python-multipart==0.0.6
pandas==2.1.3
openpyxl==3.1.2
python-jose[cryptography]==3.3.0
passlib[bcrypt]==1.7.4
python-dotenv==1.0.0
pydantic==2.5.0
pydantic-settings==2.1.0
slowapi==0.1.9
sqlparse==0.4.4

# spyMonk-DB client (install from your repo)
# For now, install from local path or publish to PyPI
# spymonk-db-enterprise @ git+https://github.com/yourusername/spyMonk-DB.git
```

#### File: `spyMonk-warehouse/backend/.env.example`
```bash
# API Security
API_SECRET_KEY=your-super-secret-key-change-this
API_KEY_HEADER=X-API-Key
ALLOWED_API_KEYS=key1,key2,key3

# CORS
CORS_ORIGINS=https://your-frontend-domain.pages.dev,https://yourdomain.com

# spyMonk-DB Connection
SPYMONK_DB_NODES=db-node1.railway.app:5000,db-node2.railway.app:5001,db-node3.railway.app:5002
SPYMONK_DB_AUTH_TOKEN=your-db-auth-token-must-match-db-env

# Rate Limiting
RATE_LIMIT_PER_MINUTE=60
UPLOAD_MAX_SIZE_MB=100

# Environment
ENVIRONMENT=production
LOG_LEVEL=INFO
```

---

### 2.3 spyMonk-warehouse Frontend Preparation

#### File: `spyMonk-warehouse/vite.config.ts`
```typescript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
  server: {
    proxy: {
      '/api': {
        target: process.env.VITE_API_URL || 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
```

#### File: `spyMonk-warehouse/.env.production`
```bash
# Backend API endpoint
VITE_API_URL=https://your-backend.railway.app
VITE_API_KEY=your-api-key-for-frontend
```

---

## 🚢 PHASE 3: DEPLOYMENT TO CLOUDFLARE + COMPUTE PLATFORMS

### Option A: Using Railway (Recommended for simplicity)

#### 3.1 Deploy spyMonk-DB to Railway

1. **Create Railway Account**: https://railway.app
2. **Install Railway CLI**:
   ```bash
   npm install -g @railway/cli
   railway login
   ```

3. **Deploy Database Cluster**:
   ```bash
   cd spyMonk-DB

   # Create new project
   railway init

   # Deploy node 1
   railway up --service db-node1
   railway variables set NODE_ID=node1
   railway variables set DB_AUTH_TOKEN=$(openssl rand -hex 32)

   # Deploy node 2
   railway up --service db-node2
   railway variables set NODE_ID=node2
   railway variables set DB_AUTH_TOKEN=<same-token-as-node1>

   # Deploy node 3
   railway up --service db-node3
   railway variables set NODE_ID=node3
   railway variables set DB_AUTH_TOKEN=<same-token-as-node1>

   # Link nodes together (set PEERS for each)
   railway variables set PEERS=db-node2.railway.internal:5000,db-node3.railway.internal:5000 --service db-node1
   railway variables set PEERS=db-node1.railway.internal:5000,db-node3.railway.internal:5000 --service db-node2
   railway variables set PEERS=db-node1.railway.internal:5000,db-node2.railway.internal:5000 --service db-node3
   ```

4. **Enable Private Networking**:
   - Go to Railway dashboard
   - Enable "Private Networking" for security
   - Only expose one node publicly for client connections

5. **Get Connection URLs**:
   ```bash
   railway domain --service db-node1
   # Output: db-node1-production.up.railway.app
   ```

#### 3.2 Deploy spyMonk-warehouse Backend to Railway

```bash
cd spyMonk-warehouse/backend

# Initialize
railway init

# Deploy
railway up

# Set environment variables
railway variables set API_SECRET_KEY=$(openssl rand -hex 32)
railway variables set SPYMONK_DB_NODES=db-node1.railway.internal:5000,db-node2.railway.internal:5000,db-node3.railway.internal:5000
railway variables set SPYMONK_DB_AUTH_TOKEN=<your-db-token>
railway variables set CORS_ORIGINS=https://your-site.pages.dev
railway variables set ALLOWED_API_KEYS=<generate-secure-key>

# Get backend URL
railway domain
# Output: warehouse-backend-production.up.railway.app
```

#### 3.3 Deploy Frontend to Cloudflare Pages

```bash
cd spyMonk-warehouse

# Build frontend
npm install
VITE_API_URL=https://warehouse-backend-production.up.railway.app npm run build

# Install Wrangler CLI
npm install -g wrangler

# Login to Cloudflare
wrangler login

# Deploy to Pages
wrangler pages deploy dist --project-name=spymonk-warehouse

# Set environment variables
wrangler pages deployment create --env production
# In Cloudflare dashboard, add:
# VITE_API_URL=https://warehouse-backend-production.up.railway.app
# VITE_API_KEY=<your-api-key>
```

---

### Option B: Using Render.com

#### 3.1 Deploy spyMonk-DB

1. Create account at https://render.com
2. Create new **Web Service** (3 instances for 3 nodes)
3. Connect your GitHub repo
4. Configure each service:
   - **Build Command**: `pip install -r requirements_enterprise.txt && pip install -e .`
   - **Start Command**: `python -m spymonk_enterprise.spanserver.server --node-id=$NODE_ID --data-dir=/data --port=5000`
   - **Environment Variables**: Same as Railway

#### 3.2 Deploy Backend

Same process, create Web Service for FastAPI backend.

#### 3.3 Frontend to Cloudflare Pages

Same as Option A.

---

### Option C: Using AWS/DigitalOcean (Advanced)

**spyMonk-DB**: Deploy on EC2/Droplets with Docker Compose
**Backend**: Deploy on Elastic Beanstalk/App Platform
**Frontend**: Cloudflare Pages

---

## 🔒 PHASE 4: SECURITY CONFIGURATION

### 4.1 Enable TLS/SSL

**Railway/Render**: Automatic HTTPS certificates ✅

**Custom domains**:
```bash
# Add custom domain in Railway
railway domain add yourdomain.com

# Configure DNS in Cloudflare
# CNAME: api.yourdomain.com -> your-backend.railway.app
```

### 4.2 Set up Cloudflare Security

1. **Dashboard → Security → WAF**: Enable Web Application Firewall
2. **Rate Limiting Rules**:
   - `/upload`: 10 requests/minute per IP
   - `/query`: 100 requests/minute per IP
3. **DDoS Protection**: Auto-enabled on Cloudflare
4. **Bot Protection**: Enable challenge for suspicious traffic

### 4.3 Database Network Security

**Railway**: Use Private Networking (only expose 1 node)
**AWS**: Use VPC with security groups
**DigitalOcean**: Use VPC and firewall rules

```bash
# Firewall rules (DigitalOcean example)
# Allow only backend IPs to access DB ports 5000-5002
doctl compute firewall create \
  --name spymonk-db-firewall \
  --inbound-rules "protocol:tcp,ports:5000-5002,sources:addresses:<backend-ip>"
```

---

## 📊 PHASE 5: MONITORING & MAINTENANCE

### 5.1 Set Up Monitoring

**Railway/Render**: Built-in metrics ✅

**Custom Monitoring**:
```bash
# Add Prometheus exporter to spyMonk-DB
# Already available in codebase at:
# spymonk_enterprise/observability/metrics/prometheus_exporter.py

# Expose metrics endpoint
railway variables set PROMETHEUS_ENABLED=true
railway variables set PROMETHEUS_PORT=9090
```

### 5.2 Logging

**Centralized logging**:
- **Railway**: Built-in log aggregation
- **External**: Set up Datadog/Sentry

```python
# Add to main.py
import sentry_sdk
sentry_sdk.init(dsn=os.getenv("SENTRY_DSN"))
```

### 5.3 Backups

**Database backups**:
```bash
# Schedule daily backups (cron job)
# In Railway, add a scheduled task:
0 2 * * * python -m spymonk_enterprise.admin.backup --output=/backups/$(date +\%Y\%m\%d).tar.gz
```

---

## 🧪 PHASE 6: TESTING DEPLOYMENT

### 6.1 Test Database Cluster

```bash
# Install client
pip install spymonk-db-enterprise

# Test connection
python << EOF
from spymonk_enterprise.client import DistributedClient

client = DistributedClient([
    "db-node1.railway.app:5000",
    "db-node2.railway.app:5001",
    "db-node3.railway.app:5002"
])

# Test write
client.put(b"test:key", b"test:value")

# Test read
value = client.get(b"test:key")
print(f"Read value: {value}")  # Should print: b"test:value"
EOF
```

### 6.2 Test Backend API

```bash
# Health check
curl https://your-backend.railway.app/health

# Upload test file (with API key)
curl -X POST https://your-backend.railway.app/upload \
  -H "X-API-Key: your-api-key" \
  -F "file=@test.csv"

# Query test
curl -X POST https://your-backend.railway.app/query \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"query": "SELECT * FROM test LIMIT 10"}'
```

### 6.3 Load Testing

```bash
# Install k6
brew install k6

# Create load test script
cat > loadtest.js << 'EOF'
import http from 'k6/http';
import { check } from 'k6';

export const options = {
  vus: 100,
  duration: '30s',
};

export default function () {
  const res = http.get('https://your-backend.railway.app/health');
  check(res, { 'status is 200': (r) => r.status === 200 });
}
EOF

# Run test
k6 run loadtest.js
```

---

## 💰 COST ESTIMATION

### Railway (Recommended)

| Service | Plan | Monthly Cost |
|---------|------|--------------|
| spyMonk-DB (3 nodes) | Hobby (512MB each) | $15 |
| Backend API | Hobby (1GB) | $5 |
| **Total** | | **$20/month** |

### Render

| Service | Plan | Monthly Cost |
|---------|------|--------------|
| spyMonk-DB (3 nodes) | Starter (512MB each) | $21 |
| Backend API | Starter (512MB) | $7 |
| **Total** | | **$28/month** |

### Cloudflare Pages

| Service | Plan | Monthly Cost |
|---------|------|--------------|
| Frontend hosting | Free (up to 500 builds/month) | $0 |
| Bandwidth | Free (unlimited) | $0 |

---

## 🚀 QUICK START CHECKLIST

- [ ] **Week 1**: Security fixes
  - [ ] Implement authentication (see SECURITY_FIXES.md)
  - [ ] Fix SQL injection
  - [ ] Configure CORS properly
  - [ ] Add rate limiting

- [ ] **Week 2**: Database deployment
  - [ ] Create Railway/Render account
  - [ ] Deploy 3 spyMonk-DB nodes
  - [ ] Configure private networking
  - [ ] Test cluster connectivity

- [ ] **Week 3**: Backend deployment
  - [ ] Update code to use DistributedClient
  - [ ] Deploy to Railway/Render
  - [ ] Configure environment variables
  - [ ] Test API endpoints

- [ ] **Week 4**: Frontend deployment
  - [ ] Build production frontend
  - [ ] Deploy to Cloudflare Pages
  - [ ] Configure custom domain
  - [ ] Enable WAF and security features

- [ ] **Week 5**: Monitoring & launch
  - [ ] Set up monitoring/logging
  - [ ] Configure backups
  - [ ] Load testing
  - [ ] Go live!

---

## 📚 Additional Resources

- [Railway Documentation](https://docs.railway.app/)
- [Cloudflare Pages Docs](https://developers.cloudflare.com/pages/)
- [FastAPI Security](https://fastapi.tiangolo.com/tutorial/security/)
- [spyMonk-DB Architecture](./spyMonk-DB/ARCHITECTURE.md)

---

## ⚠️ IMPORTANT NOTES

1. **Never commit secrets** to git (use .env files, add to .gitignore)
2. **Always use HTTPS** for production
3. **Backup your database** regularly
4. **Monitor logs** for suspicious activity
5. **Keep dependencies updated** for security patches

---

Need help? Open an issue or contact: your-email@example.com
