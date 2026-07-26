-- ============================================================================
-- 006_notifications.sql — in-app notification centre (FR-2.5)
--
-- Backs the topbar bell. Every call to services.notify.send() now writes a
-- row here IN ADDITION to the log line and the optional SMTP delivery, so
-- the three delivery channels share one call site and one contract.
--
-- Column types match the deployed schema: User.userID is int(10) unsigned.
-- ============================================================================

CREATE TABLE IF NOT EXISTS Notification (
    notificationID INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    userID         INT UNSIGNED NOT NULL,
    subject        VARCHAR(255) NOT NULL,
    body           TEXT         NOT NULL,

    -- Relative path this notification points at, e.g. /tickets/42. Nullable:
    -- not every notification has somewhere to go (an account approval, say).
    link           VARCHAR(255) NULL DEFAULT NULL,

    -- NULL = unread. A nullable timestamp rather than a boolean, so the
    -- audit question "when did they see this?" is answerable.
    readAt         DATETIME     NULL DEFAULT NULL,
    createdAt      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_notif_user FOREIGN KEY (userID)
        REFERENCES User(userID) ON DELETE CASCADE,

    -- Serves both the unread badge (userID + readAt) and the dropdown's
    -- newest-first ordering, in one index.
    INDEX idx_notif_user (userID, readAt, createdAt)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ---------------------------------------------------------------------------
-- Verification
-- ---------------------------------------------------------------------------
-- SHOW CREATE TABLE Notification;
-- SELECT userID, COUNT(*) total, SUM(readAt IS NULL) unread
--   FROM Notification GROUP BY userID;
