# Render Environment Variables Setup

## Required Environment Variables

When deploying to Render, set these environment variables in the Render dashboard:

### 1. MONGO_URL
**Required**: Yes
**Description**: MongoDB connection string
**Example**: `mongodb+srv://username:password@cluster.mongodb.net/`

**How to get it:**
1. Create a MongoDB Atlas account at https://www.mongodb.com/cloud/atlas
2. Create a new cluster (free tier M0 available)
3. Click "Connect" on your cluster
4. Choose "Connect your application"
5. Copy the connection string
6. Replace `<password>` with your database user password

### 2. DB_NAME
**Required**: Yes
**Description**: MongoDB database name
**Default**: `chamake_db`
**Example**: `chamake_db`

### 3. SECRET_KEY
**Required**: Yes
**Description**: Secret key for JWT token encryption
**Example**: `super-secret-key-that-should-be-at-least-32-characters-long`

**How to generate:**
```python
import secrets
print(secrets.token_urlsafe(32))
```

Or use: `openssl rand -base64 32`

### 4. PORT
**Required**: No (Render sets this automatically)
**Description**: Port the server will run on
**Note**: Render automatically provides this variable

## Setting Environment Variables in Render

1. Go to your service dashboard in Render
2. Click on "Environment" tab
3. Click "Add Environment Variable"
4. Add each variable with its key and value
5. Click "Save Changes"
6. Service will automatically redeploy

## Security Notes

⚠️ **NEVER** commit `.env` file with real credentials to Git
✅ Use strong, unique passwords for MongoDB
✅ Generate a random SECRET_KEY (min 32 characters)
✅ Keep SECRET_KEY secure and never share it

## MongoDB Atlas Setup Checklist

- [ ] Created MongoDB Atlas account
- [ ] Created cluster
- [ ] Created database user with strong password
- [ ] Whitelisted all IPs (0.0.0.0/0) for Render access
- [ ] Copied connection string
- [ ] Replaced `<password>` in connection string
- [ ] Added MONGO_URL to Render environment variables
