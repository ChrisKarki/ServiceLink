-- ============================================================================
-- 005_attachments.sql — FR-2.1 ticket attachments
--
-- Column types match the deployed schema exactly:
--   User.userID   int(10) unsigned
--   Ticket.ticketID — confirm with SHOW CREATE TABLE Ticket; the FK below
--   assumes int(10) unsigned to match User. If it differs, adjust ticketID
--   here to match, or errno 150 will bite the same way it did on 004.
-- ============================================================================

CREATE TABLE IF NOT EXISTS TicketAttachment (
    attachmentID   INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    ticketID       INT UNSIGNED NOT NULL,

    -- The name the user saw on their own machine. Displayed, never used to
    -- build a path — see storedName.
    originalName   VARCHAR(255) NOT NULL,

    -- Opaque generated filename on disk (uuid4 + extension). Decouples what
    -- we display from what we open, so a hostile filename cannot traverse
    -- out of the upload directory or collide with another user's file.
    storedName     VARCHAR(80)  NOT NULL,

    mimeType       VARCHAR(100) NOT NULL,
    sizeBytes      INT UNSIGNED NOT NULL,
    uploadedByID   INT UNSIGNED NOT NULL,
    uploadedAt     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_attach_ticket FOREIGN KEY (ticketID)
        REFERENCES Ticket(ticketID) ON DELETE CASCADE,
    CONSTRAINT fk_attach_user FOREIGN KEY (uploadedByID)
        REFERENCES User(userID) ON DELETE RESTRICT,
    UNIQUE KEY uq_stored (storedName),
    INDEX idx_attach_ticket (ticketID, uploadedAt)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ---------------------------------------------------------------------------
-- AuditLog.entityType needs 'TicketAttachment'
-- ---------------------------------------------------------------------------
-- audit.py ALSO keeps its own allowlist — adding the value here is only half
-- the job. See the note in the delivery message.

ALTER TABLE AuditLog MODIFY COLUMN entityType
    ENUM('Ticket','Resource','User','KBArticle','TicketComment',
         'TicketResource','Category','SLAPolicy','SystemSetting',
         'TicketAttachment')
    NOT NULL;


-- ---------------------------------------------------------------------------
-- Verification
-- ---------------------------------------------------------------------------
-- SHOW CREATE TABLE TicketAttachment;
-- SHOW COLUMNS FROM AuditLog LIKE 'entityType';
