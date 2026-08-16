# 📦 spyMonk-DB Packaging & Private PyPI Guide

Complete guide to package spyMonk-DB as a wheel (.whl) and distribute via private PyPI registry.

---

## 🎯 Overview

This guide covers:
1. **Building** spyMonk-DB as a distributable `.whl` package
2. **Setting up** a private PyPI server
3. **Uploading** packages to the registry
4. **Installing** from private PyPI in warehouse backend

---

## 📁 What Was Created

### In spyMonk-DB/

| File | Purpose |
|------|---------|
| `pyproject.toml` | Modern Python package configuration |
| `MANIFEST.in` | Specifies which files to include in package |
| `build_package.sh` | Automated build script |
| `private-pypi/docker-compose.yml` | Private PyPI server setup |
| `private-pypi/setup-pypi.sh` | PyPI server initialization |
| `private-pypi/configure-pip.sh` | Configure pip to use private server |
| `private-pypi/upload-package.sh` | Upload packages to server |

### In spyMonk-warehouse/backend/

| File | Purpose |
|------|---------|
| `requirements.txt` | Updated to use `spymonk-db-enterprise==0.1.0` |
| `pip.conf` | Example pip configuration for private PyPI |

---

## 🚀 STEP 1: Build the Package

### 1.1 Navigate to spyMonk-DB

```bash
cd /Users/shubhajeetpradhan/Desktop/idea/spyMonk-query-platform/spyMonk-DB
```

### 1.2 Run the Build Script

```bash
./build_package.sh
```

This will:
- ✅ Clean previous builds
- ✅ Install build dependencies
- ✅ Build wheel package (`.whl`)
- ✅ Build source distribution (`.tar.gz`)
- ✅ Verify package integrity

### 1.3 Verify the Build

```bash
ls -lh dist/
```

You should see:
```
spymonk_db_enterprise-0.1.0-py3-none-any.whl  # The wheel package
spymonk_db_enterprise-0.1.0.tar.gz            # Source distribution
```

### 1.4 Test Installation Locally

```bash
# Create a test virtual environment
python -m venv test_env
source test_env/bin/activate

# Install the wheel
pip install dist/spymonk_db_enterprise-0.1.0-py3-none-any.whl

# Test import
python -c "from spymonk_enterprise.client import SpyMonkClient; print('✅ Package works!')"

# Cleanup
deactivate
rm -rf test_env
```

---

## 🗄️ STEP 2: Set Up Private PyPI Server

### Option A: Local Development (Docker)

#### 2.1 Navigate to Private PyPI Directory

```bash
cd private-pypi
```

#### 2.2 Run Setup Script

```bash
./setup-pypi.sh
```

This will:
1. Create necessary directories
2. Prompt for username/password
3. Generate authentication file
4. Start Docker container with PyPI server

Example interaction:
```
Enter username for PyPI server (e.g., spymonk):
> spymonk

Enter password:
> [your-secure-password]

✅ Server started at http://localhost:8080
```

#### 2.3 Verify Server is Running

```bash
curl http://localhost:8080

# Or open in browser:
open http://localhost:8080
```

### Option B: Production Deployment (Railway/Cloud)

#### Deploy PyPI Server to Railway

1. **Create Dockerfile in `private-pypi/`**:

```dockerfile
# private-pypi/Dockerfile
FROM pypiserver/pypiserver:latest

# Create packages directory
RUN mkdir -p /data/packages

# Copy htpasswd if exists
COPY htpasswd /data/.htpasswd 2>/dev/null || true

EXPOSE 8080

CMD ["run", "--passwords", "/data/.htpasswd", "--authenticate", "upload", "--port", "8080", "/data/packages"]
```

2. **Deploy to Railway**:

```bash
cd private-pypi
railway init
railway up

# Set environment variables
railway variables set PYPISERVER_PASSWORDS=/data/.htpasswd

# Get the public URL
railway domain
# Output: pypi-server.up.railway.app
```

3. **Upload htpasswd file**:

```bash
# Generate htpasswd locally first
./setup-pypi.sh

# Then upload via Railway CLI or dashboard
```

---

## ⬆️ STEP 3: Upload Package to Private PyPI

### 3.1 Run Upload Script

```bash
cd /Users/shubhajeetpradhan/Desktop/idea/spyMonk-query-platform/spyMonk-DB/private-pypi
./upload-package.sh
```

Prompts:
```
Enter PyPI server URL (default: http://localhost:8080):
> http://localhost:8080  (or your-pypi-server.railway.app)

Enter username:
> spymonk

Enter password:
> [your-password]
```

### 3.2 Manual Upload (Alternative)

```bash
cd ../  # Back to spyMonk-DB root

pip install twine

# For local server
twine upload --repository-url http://localhost:8080 \
  --username spymonk \
  --password your-password \
  dist/*

# For production server
twine upload --repository-url https://pypi-server.railway.app \
  --username spymonk \
  --password your-password \
  dist/*
```

### 3.3 Verify Upload

```bash
# List packages on server
curl http://spymonk:your-password@localhost:8080/simple/

# Or visit in browser:
open http://localhost:8080/simple/
```

You should see `spymonk-db-enterprise` listed.

---

## 📥 STEP 4: Configure Warehouse Backend to Use Private PyPI

### 4.1 Configure Pip Globally

```bash
cd spyMonk-DB/private-pypi
./configure-pip.sh
```

This creates `~/.pip/pip.conf`:

```ini
[global]
extra-index-url = http://spymonk:password@localhost:8080/simple/
trusted-host = localhost
```

### 4.2 Test Installation from Private PyPI

```bash
# In warehouse backend directory
cd /Users/shubhajeetpradhan/Desktop/idea/spyMonk-query-platform/spyMonk-warehouse/backend

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install from private PyPI
pip install spymonk-db-enterprise==0.1.0

# Verify
python -c "from spymonk_enterprise.client import SpyMonkClient; print('✅ Installed from private PyPI!')"
```

### 4.3 For Production (Railway/Render)

When deploying backend to Railway/Render, configure pip using environment variables:

#### Railway

1. **Create `Dockerfile` in backend/**:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Copy requirements
COPY requirements.txt .

# Configure pip for private PyPI
ARG PYPI_URL
ARG PYPI_USERNAME
ARG PYPI_PASSWORD

RUN pip config set global.extra-index-url http://${PYPI_USERNAME}:${PYPI_PASSWORD}@${PYPI_URL}/simple/
RUN pip config set global.trusted-host ${PYPI_URL}

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

2. **Set Railway environment variables**:

```bash
railway variables set PYPI_URL=pypi-server.railway.app
railway variables set PYPI_USERNAME=spymonk
railway variables set PYPI_PASSWORD=your-password
```

#### Alternative: Use pip.conf at Runtime

Create `backend/.pip/pip.conf`:

```ini
[global]
extra-index-url = http://${PYPI_USERNAME}:${PYPI_PASSWORD}@${PYPI_URL}/simple/
trusted-host = ${PYPI_URL}
```

Then in Dockerfile:

```dockerfile
# Substitute environment variables
RUN envsubst < .pip/pip.conf.template > ~/.pip/pip.conf
```

---

## 🔐 STEP 5: Secure Your Private PyPI

### 5.1 Use Strong Passwords

```bash
# Generate secure password
openssl rand -base64 32
```

### 5.2 Use HTTPS in Production

Add SSL certificate to your PyPI server:

**For Railway**: HTTPS is automatic ✅

**For custom server**: Use Nginx with Let's Encrypt:

```nginx
server {
    listen 443 ssl;
    server_name pypi.yourcompany.com;

    ssl_certificate /etc/letsencrypt/live/pypi.yourcompany.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/pypi.yourcompany.com/privkey.pem;

    location / {
        proxy_pass http://localhost:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### 5.3 Restrict Access

Update `docker-compose.yml`:

```yaml
services:
  pypi-server:
    # ... other config ...
    command: >
      run
      --passwords /data/.htpasswd
      --authenticate download,list,upload  # Require auth for everything
      --port 8080
      /data/packages
```

---

## 🔄 STEP 6: Version Management

### 6.1 Update Package Version

Edit `pyproject.toml`:

```toml
[project]
version = "0.2.0"  # Increment version
```

### 6.2 Rebuild and Upload

```bash
cd spyMonk-DB
./build_package.sh

cd private-pypi
./upload-package.sh
```

### 6.3 Update Warehouse Backend

```bash
# In warehouse backend/requirements.txt
spymonk-db-enterprise==0.2.0  # Update version
```

---

## 📊 Usage Summary

### One-Time Setup

```bash
# 1. Build package
cd spyMonk-DB
./build_package.sh

# 2. Start PyPI server
cd private-pypi
./setup-pypi.sh

# 3. Upload package
./upload-package.sh

# 4. Configure pip
./configure-pip.sh
```

### Daily Workflow

```bash
# Install in warehouse backend
cd spyMonk-warehouse/backend
pip install spymonk-db-enterprise==0.1.0

# Or install in any project
pip install spymonk-db-enterprise
```

### Update Workflow

```bash
# 1. Make changes to spyMonk-DB
# 2. Update version in pyproject.toml
# 3. Rebuild
./build_package.sh

# 4. Upload new version
cd private-pypi
./upload-package.sh

# 5. Update warehouse backend requirements.txt
```

---

## 🆚 Comparison: Installation Methods

| Method | Pros | Cons | Best For |
|--------|------|------|----------|
| **Local Path** (`-e ../../spyMonk-DB`) | Easy development, instant changes | Not portable, requires source | Development |
| **Private PyPI** (`pip install spymonk-db-enterprise`) | Professional, versioned, portable | Requires server setup | Production |
| **GitHub** (`git+https://github.com/...`) | No server needed, easy sharing | Slower, requires Git access | Small teams |
| **Public PyPI** (`pip install spymonk-db`) | Easiest for users | Code is public | Open source |

---

## 🐛 Troubleshooting

### Package Build Fails

```bash
# Install build dependencies
pip install --upgrade setuptools wheel build

# Check pyproject.toml syntax
python -c "import tomllib; tomllib.load(open('pyproject.toml', 'rb'))"
```

### PyPI Server Won't Start

```bash
# Check Docker is running
docker ps

# View logs
docker-compose logs -f

# Restart server
docker-compose restart
```

### Can't Install from Private PyPI

```bash
# Test connectivity
curl http://localhost:8080/simple/

# Check pip configuration
pip config list

# Try with explicit URL
pip install --extra-index-url http://spymonk:password@localhost:8080/simple/ spymonk-db-enterprise

# Check credentials
cat ~/.pip/pip.conf
```

### Wrong Version Installed

```bash
# Clear pip cache
pip cache purge

# Force reinstall
pip install --force-reinstall --no-cache-dir spymonk-db-enterprise==0.1.0
```

---

## 📚 Additional Resources

### PyPI Server Management

```bash
# View all packages
curl http://localhost:8080/packages/

# Remove a package
rm private-pypi/packages/spymonk_db_enterprise-0.1.0-py3-none-any.whl
docker-compose restart

# Backup packages
tar -czf pypi-backup-$(date +%Y%m%d).tar.gz private-pypi/packages/
```

### Advanced Configuration

**Whitelist IPs** (add to `docker-compose.yml`):

```yaml
environment:
  - PYPISERVER_WHITELIST=192.168.1.0/24,10.0.0.0/8
```

**Enable logging**:

```yaml
volumes:
  - ./logs:/var/log/pypiserver
command: >
  run
  --log-file /var/log/pypiserver/access.log
  /data/packages
```

---

## ✅ Final Checklist

- [ ] Built spyMonk-DB wheel package
- [ ] Started private PyPI server (local or cloud)
- [ ] Uploaded package to PyPI server
- [ ] Configured pip to use private PyPI
- [ ] Updated warehouse backend requirements.txt
- [ ] Tested installation from private PyPI
- [ ] Secured PyPI server with authentication
- [ ] (Production) Deployed PyPI server to Railway
- [ ] (Production) Configured HTTPS for PyPI server
- [ ] Documented credentials securely

---

## 🎉 Summary

You now have:

✅ **Packaged** spyMonk-DB as a distributable wheel
✅ **Private PyPI server** running (local or cloud)
✅ **Automated scripts** for build, upload, and configure
✅ **Warehouse backend** configured to install from private PyPI
✅ **Version management** for professional package distribution

**Next Steps:**
1. Deploy PyPI server to production (Railway)
2. Push warehouse backend to production
3. Test end-to-end deployment
4. Set up automated CI/CD for package building

---

Need help? Questions? Check [DEPLOYMENT_ROADMAP.md](./DEPLOYMENT_ROADMAP.md) for full deployment guide!
