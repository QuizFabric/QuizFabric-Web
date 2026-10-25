# Production Deployment Guide

## Changes Made for Production

### 1. **Debug Mode Disabled**
   - Changed `debug=True` to `debug=False` in app startup
   - Disables Flask development features that expose security vulnerabilities

### 2. **Updated Requirements**
   - Added `gunicorn[gevent]` - production-grade WSGI server
   - Added `python-dotenv` - for environment variable management

### 3. **WSGI Entry Point** (`wsgi.py`)
   - Created standard WSGI application entry point
   - Use with `gunicorn` for production deployment

### 4. **Environment Configuration** (`.env.production`)
   - Set `FLASK_ENV=production`
   - Set `FLASK_DEBUG=0` (disabled)
   - Define secure secret key (CHANGE THIS BEFORE DEPLOYING)

### 5. **Production Startup Script** (`run_production.bat`)
   - Uses gunicorn with multiple workers for load distribution
   - Configured for gevent compatibility with SocketIO

## Deployment Instructions

### Option 1: Using Gunicorn (Recommended)
```bash
pip install -r requirements.txt
gunicorn --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker --bind 0.0.0.0:5710 --workers 4 wsgi:app
```

### Option 2: Using Run Script (Windows)
```bash
run_production.bat
```

### Before Going Live

1. **Configure Session Security**
   - Set `FLASK_SECRET_KEY` in `.env.production` to a secure random value shared by every worker.
   - Generate one with: `python -c "import secrets; print(secrets.token_hex(32))"`
   - If it is not configured, the app creates and reuses `data/session_secret.key`. Keep that file private and persistent across restarts.

2. **Configure Account Registration**
   - The app requires a registered account for pages, APIs, quiz actions, and Socket.IO connections.
   - Usernames are unique, passwords are stored as password hashes, and registration is limited to one attempt per source IP per day.
   - The public `/join` page and player connections remain available to unauthenticated quiz participants; hosting or controlling a game still requires login.
   - Registration throttling uses the direct connection address and does not trust `X-Forwarded-For`; this expects Cloudflare DNS-only mode, not a proxy.
   - Account records are stored in `data/logins.json`; this file and its lock file are gitignored and must be backed up privately.
   - The persistent login is a 30-day `HttpOnly`, `Secure`, `SameSite=Lax` cookie. HTTPS is required.

3. **Configure the Administrator (Optional)**
   - Set both `QUIZFABRIC_ADMIN_USERNAME` and a strong `QUIZFABRIC_ADMIN_PASSWORD` before registering the administrator account.
   - The configured username is reserved; the matching configured password is required to register it with administrator privileges.
   - Only that account can access IP management, logs, and other administrator endpoints.

4. **Set Environment Variable**
   - `set FLASK_ENV=production`
   - Or use `.env.production` file

5. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

6. **Enable HTTPS**
   - Configure with reverse proxy (nginx/Apache)
   - Use SSL/TLS certificates

7. **Configure Logging**
   - Redirect logs to a file: `gunicorn ... --access-logfile access.log --error-logfile error.log`

8. **Performance Tuning**
   - Adjust `--workers` based on CPU cores: `(2 × CPU_cores) + 1`
   - Current config: 4 workers

## Additional Security Recommendations

- [ ] Set up firewall rules
- [ ] Use environment variables for sensitive data
- [ ] Enable CORS only for trusted origins
- [ ] Implement rate limiting
- [ ] Use HTTPS/TLS
- [ ] Regular security updates
- [ ] Monitoring and alerting setup

## Differences from Development

| Feature | Development | Production |
|---------|-------------|-----------|
| Debug Mode | Enabled | Disabled |
| Auto-reload | Yes | No |
| Error Pages | Detailed | Generic |
| Server | Flask dev server | Gunicorn (WSGI) |
| Workers | 1 | 4+ |
| Performance | Optimized for dev | Optimized for scale |
