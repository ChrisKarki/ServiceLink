-- ============================================================================
-- 004_auth_mfa.sql — FR-1.1 / FR-1.2 completion
--
-- Adds:
--   * TOTP enrolment columns on User
--   * SystemSetting  — runtime config, currently the MFA on/off switch
--   * PasswordResetOTP — hashed, expiring, single-use reset codes
--
-- Idempotent where MariaDB allows it. Run once against `servicelink`.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. TOTP enrolment on User
-- ---------------------------------------------------------------------------
-- totpSecret is the base32 shared secret. NULL means "not enrolled", which is
-- what forces the enrolment flow on next sign-in while MFA is required.
-- Clearing this column is exactly what "reset MFA" means, so a password reset
-- and an Administrator reset are the same one-line operation.

-- NOTE: User.mfaEnabled ALREADY EXISTS in the deployed schema (admin.py's
-- _USER_SELECT reads it). It is the PER-USER flag and is kept as-is. The two
-- columns below hold the actual shared secret, which mfaEnabled never did.
--
-- Effective requirement at sign-in:
--     SystemSetting.mfa_required = 1   OR   User.mfaEnabled = 1
-- Enrolling sets mfaEnabled = 1; resetting clears it back to 0.
--
-- Confirm before running:  SHOW COLUMNS FROM User LIKE 'mfa%';

ALTER TABLE User
    ADD COLUMN totpSecret     VARCHAR(64) NULL DEFAULT NULL AFTER mfaEnabled,
    ADD COLUMN totpEnrolledAt DATETIME    NULL DEFAULT NULL AFTER totpSecret;


-- ---------------------------------------------------------------------------
-- 2. SystemSetting — runtime configuration
-- ---------------------------------------------------------------------------
-- Key/value rather than a column-per-setting so adding the next toggle is an
-- INSERT, not a migration. Values are strings; callers coerce.

CREATE TABLE IF NOT EXISTS SystemSetting (
    settingKey   VARCHAR(64)  NOT NULL PRIMARY KEY,
    settingValue VARCHAR(255) NOT NULL,
    updatedByID  INT          NULL,
    updatedAt    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
                              ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_setting_user FOREIGN KEY (updatedByID)
        REFERENCES User(userID) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- MFA is mandatory by default. Administrators may flip this at
-- /admin/security; the change is audited like any other privileged write.
INSERT INTO SystemSetting (settingKey, settingValue)
VALUES ('mfa_required', '1')
ON DUPLICATE KEY UPDATE settingKey = settingKey;


-- ---------------------------------------------------------------------------
-- 3. PasswordResetOTP
-- ---------------------------------------------------------------------------
-- The code is stored bcrypt-hashed, never in plaintext: a database read must
-- not hand an attacker a working reset code. Rows are single-use (usedAt),
-- time-boxed (expiresAt), and attempt-capped (attempts) so a 6-digit code
-- cannot be brute-forced within its window.

CREATE TABLE IF NOT EXISTS PasswordResetOTP (
    otpID     INT AUTO_INCREMENT PRIMARY KEY,
    userID    INT          NOT NULL,
    codeHash  VARCHAR(255) NOT NULL,
    expiresAt DATETIME     NOT NULL,
    usedAt    DATETIME     NULL DEFAULT NULL,
    attempts  INT          NOT NULL DEFAULT 0,
    createdAt DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    requestIP VARCHAR(45)  NULL,
    CONSTRAINT fk_otp_user FOREIGN KEY (userID)
        REFERENCES User(userID) ON DELETE CASCADE,
    INDEX idx_otp_lookup (userID, usedAt, expiresAt)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------------
-- 4. AuditLog.entityType — check before running the ALTER below
-- ---------------------------------------------------------------------------
-- The MFA toggle audits against entityType 'SystemSetting'. If entityType is
-- a VARCHAR, nothing to do. If it is an ENUM, it must be widened or the
-- INSERT fails. Run this first:
--
--     SHOW COLUMNS FROM AuditLog LIKE 'entityType';
--
-- If the Type column reads enum(...), re-issue that exact list with the two
-- values below appended, e.g.:
--
--     ALTER TABLE AuditLog MODIFY COLUMN entityType
--         ENUM('Ticket','Resource','User','KBArticle','TicketComment',
--              'TicketResource','Category','SLAPolicy','SystemSetting')
--         NOT NULL;
--
-- Note 'Category' and 'SLAPolicy' in that list — admin.py already writes both
-- and they may be missing from the deployed ENUM too.


-- ---------------------------------------------------------------------------
-- 5. Verification
-- ---------------------------------------------------------------------------
-- SHOW COLUMNS FROM User LIKE 'totp%';
-- SELECT * FROM SystemSetting;
-- SHOW CREATE TABLE PasswordResetOTP;
