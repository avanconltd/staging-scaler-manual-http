-- Login + audit tables for staging-env-scaler. Safe to run more than once.

IF OBJECT_ID('dbo.ScalerUsers', 'U') IS NULL
CREATE TABLE dbo.ScalerUsers (
    username        NVARCHAR(100) NOT NULL PRIMARY KEY,
    password_hash   VARCHAR(128)  NOT NULL,  -- hex PBKDF2-HMAC-SHA256
    salt            VARCHAR(64)   NOT NULL,  -- hex
    iterations      INT           NOT NULL,
    is_active       BIT           NOT NULL CONSTRAINT DF_ScalerUsers_active DEFAULT 1,
    created_at_utc  DATETIME2     NOT NULL CONSTRAINT DF_ScalerUsers_created DEFAULT SYSUTCDATETIME()
);

IF OBJECT_ID('dbo.ScalerAuditLog', 'U') IS NULL
CREATE TABLE dbo.ScalerAuditLog (
    id               BIGINT IDENTITY(1,1) PRIMARY KEY,
    invoked_at_utc   DATETIME2     NOT NULL CONSTRAINT DF_ScalerAuditLog_invoked DEFAULT SYSUTCDATETIME(),
    finished_at_utc  DATETIME2     NULL,
    username         NVARCHAR(100) NULL,     -- name sent by the caller, even when login failed
    auth_ok          BIT           NOT NULL,
    client_ip        VARCHAR(64)   NULL,
    user_agent       NVARCHAR(400) NULL,
    scale            NVARCHAR(20)  NULL,
    de               BIT           NULL,
    web              BIT           NULL,
    http_status      INT           NULL,     -- NULL = still running / timed out
    duration_ms      INT           NULL,
    result_json      NVARCHAR(MAX) NULL
);

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ScalerAuditLog_invoked')
CREATE INDEX IX_ScalerAuditLog_invoked ON dbo.ScalerAuditLog (invoked_at_utc DESC);
