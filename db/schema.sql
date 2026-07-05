-- =====================================================================
-- ServiceLink — Physical Database Schema (DDL)
-- INFO 2413 S50 · Group A · Phase 4 implementation of the Phase 3 design
-- Target: MySQL 8 / MariaDB 10.6+, InnoDB, utf8mb4
--
-- Implements the 12-relation, 3NF schema from §3.4 (Final Relational
-- Schema), §4.1 (Field Design), and §4.3 (Data Integrity Controls).
-- Tables are ordered so every FOREIGN KEY references an existing table:
--   parents first (User, Category, SLAPolicy, KBArticle), then Ticket,
--   then Ticket's dependents, then Resource/TicketResource, then audit.
--
-- Run as the schema owner (root), not as servicelink_app:
--   mysql -u root -p servicelink < schema.sql
-- =====================================================================

SET NAMES utf8mb4;

-- ---------------------------------------------------------------------
-- 1. User (strong)
-- ---------------------------------------------------------------------
CREATE TABLE User (
    userID        INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    email         VARCHAR(254) NOT NULL UNIQUE,
    passwordHash  CHAR(60)     NOT NULL,                 -- bcrypt, NFR-S1
    firstName     VARCHAR(50)  NOT NULL,
    lastName      VARCHAR(50)  NOT NULL,
    role          ENUM('EndUser','Technician','Manager','Administrator') NOT NULL,
    status        ENUM('PendingApproval','Active','Suspended')
                               NOT NULL DEFAULT 'PendingApproval',       -- FR-1.2
    mfaEnabled    BOOLEAN      NOT NULL DEFAULT FALSE,                   -- FR-1.1
    createdAt     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    lastLoginAt   DATETIME     NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- 2. Category (configurable lookup, FR-6.1)
-- ---------------------------------------------------------------------
CREATE TABLE Category (
    categoryID  INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(80) NOT NULL UNIQUE,
    isActive    BOOLEAN     NOT NULL DEFAULT TRUE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- 3. SLAPolicy (lookup keyed on the natural key priority, FR-2.5 / FR-6.1)
--    The ENUM definition must stay identical to Ticket.priority so the
--    FK enforces correctly (InnoDB compares ENUMs by internal index).
-- ---------------------------------------------------------------------
CREATE TABLE SLAPolicy (
    priority              ENUM('Low','Medium','High','Critical') PRIMARY KEY,
    responseTargetMins    INT UNSIGNED NOT NULL,
    resolutionTargetMins  INT UNSIGNED NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- 4. KBArticle (strong; referenced by Ticket, so created before it)
-- ---------------------------------------------------------------------
CREATE TABLE KBArticle (
    articleID     INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    title         VARCHAR(150) NOT NULL,
    body          MEDIUMTEXT   NOT NULL,
    authorID      INT UNSIGNED NOT NULL,
    approvedByID  INT UNSIGNED NULL,                     -- null until approval
    status        ENUM('Draft','PendingApproval','Published','Archived')
                               NOT NULL DEFAULT 'Draft',
    visibility    ENUM('Internal','Public') NOT NULL DEFAULT 'Internal',
    createdAt     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    publishedAt   DATETIME     NULL,
    FOREIGN KEY (authorID)     REFERENCES User(userID),
    FOREIGN KEY (approvedByID) REFERENCES User(userID)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- 5. Ticket (core entity; dual FK to User for submitter vs. assignee)
-- ---------------------------------------------------------------------
CREATE TABLE Ticket (
    ticketID           INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    title              VARCHAR(150) NOT NULL,
    description        TEXT         NOT NULL,
    categoryID         INT UNSIGNED NOT NULL,
    priority           ENUM('Low','Medium','High','Critical') NOT NULL,
    status             ENUM('New','Assigned','InProgress',
                            'WaitingOnUser','Resolved','Closed')
                                    NOT NULL DEFAULT 'New',
    submittedByUserID  INT UNSIGNED NOT NULL,
    assignedToUserID   INT UNSIGNED NULL,                -- null when unassigned
    createdAt          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolvedAt         DATETIME     NULL,
    slaBreached        BOOLEAN      NOT NULL DEFAULT FALSE,  -- stored, not derived (§4.2)
    resolutionSummary  TEXT         NULL,
    linkedKBArticleID  INT UNSIGNED NULL,
    FOREIGN KEY (categoryID)        REFERENCES Category(categoryID),
    FOREIGN KEY (priority)          REFERENCES SLAPolicy(priority),
    FOREIGN KEY (submittedByUserID) REFERENCES User(userID),
    FOREIGN KEY (assignedToUserID)  REFERENCES User(userID)
        ON DELETE SET NULL,                              -- §4.3: technician removed
    FOREIGN KEY (linkedKBArticleID) REFERENCES KBArticle(articleID)
        ON DELETE SET NULL                               -- §4.3: article deleted
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- 6. TicketComment (weak entity; existence depends on Ticket)
-- ---------------------------------------------------------------------
CREATE TABLE TicketComment (
    commentID     INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    ticketID      INT UNSIGNED NOT NULL,
    authorUserID  INT UNSIGNED NOT NULL,
    commentType   ENUM('Internal','Public') NOT NULL,    -- FR-2.4
    bodyText      TEXT         NOT NULL,
    createdAt     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (ticketID)     REFERENCES Ticket(ticketID)
        ON DELETE CASCADE,                               -- §4.3
    FOREIGN KEY (authorUserID) REFERENCES User(userID)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- 7. Attachment
-- ---------------------------------------------------------------------
CREATE TABLE Attachment (
    attachmentID   INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    ticketID       INT UNSIGNED NOT NULL,
    fileName       VARCHAR(255) NOT NULL,
    fileType       VARCHAR(100) NOT NULL,
    fileSizeBytes  INT UNSIGNED NOT NULL,                -- bounded by app (UC-01 AF-2)
    uploadedAt     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (ticketID) REFERENCES Ticket(ticketID)
        ON DELETE CASCADE                                -- §4.3
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- 8. Resource ("Resource", never "Asset" — locked terminology)
-- ---------------------------------------------------------------------
CREATE TABLE Resource (
    resourceID      INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    resourceTag     VARCHAR(40)  NOT NULL UNIQUE,        -- human-readable key
    type            ENUM('Hardware','Software','Virtual') NOT NULL,
    make            VARCHAR(60)  NOT NULL,               -- decomposed from makeModel (§3.1)
    model           VARCHAR(60)  NOT NULL,
    serialNumber    VARCHAR(80)  NULL,
    assignedUserID  INT UNSIGNED NULL,
    status          ENUM('InUse','InStock','Disposed','LostMissing') NOT NULL,
    location        VARCHAR(120) NOT NULL,
    purchaseDate    DATE         NULL,
    warrantyEndDate DATE         NULL,
    createdAt       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updatedAt       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
                                 ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (assignedUserID) REFERENCES User(userID)
        ON DELETE SET NULL,
    CONSTRAINT chk_warranty_after_purchase                -- §4.3 range control
        CHECK (warrantyEndDate IS NULL OR purchaseDate IS NULL
               OR warrantyEndDate >= purchaseDate)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- 9. TicketResource (associative; the only M:N, FR-3.2)
--    Composite PK blocks duplicate links at the DB layer (UC-03 AF-2).
-- ---------------------------------------------------------------------
CREATE TABLE TicketResource (
    ticketID        INT UNSIGNED NOT NULL,
    resourceID      INT UNSIGNED NOT NULL,
    linkedByUserID  INT UNSIGNED NOT NULL,
    linkedAt        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticketID, resourceID),
    FOREIGN KEY (ticketID)       REFERENCES Ticket(ticketID)
        ON DELETE CASCADE,                               -- §4.3: no meaning without
    FOREIGN KEY (resourceID)     REFERENCES Resource(resourceID)
        ON DELETE CASCADE,                               --       both parents
    FOREIGN KEY (linkedByUserID) REFERENCES User(userID)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- 10. ArticleTag (1NF extraction of KBArticle.tags, §3.3)
-- ---------------------------------------------------------------------
CREATE TABLE ArticleTag (
    articleID  INT UNSIGNED NOT NULL,
    tag        VARCHAR(40)  NOT NULL,
    PRIMARY KEY (articleID, tag),
    FOREIGN KEY (articleID) REFERENCES KBArticle(articleID)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- 11. AuditLog (immutable once written; never cascaded, NFR-S6)
-- ---------------------------------------------------------------------
CREATE TABLE AuditLog (
    logID       INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    actorID     INT UNSIGNED NOT NULL,
    entityType  ENUM('Ticket','Resource','User','KBArticle',
                     'TicketComment','TicketResource') NOT NULL,
    entityID    INT UNSIGNED NOT NULL,
    action      ENUM('Create','Update','Delete','Link','Unlink') NOT NULL,
    timestamp   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ipAddress   VARCHAR(45)  NOT NULL,                   -- IPv6-capable
    FOREIGN KEY (actorID) REFERENCES User(userID)
        ON DELETE RESTRICT                               -- audit rows retained for life
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- 12. AuditLogChange (1NF extraction of AuditLog.changedFields, §3.3)
-- ---------------------------------------------------------------------
CREATE TABLE AuditLogChange (
    logID      INT UNSIGNED NOT NULL,
    fieldName  VARCHAR(64)  NOT NULL,
    oldValue   TEXT         NULL,
    newValue   TEXT         NULL,
    PRIMARY KEY (logID, fieldName),
    FOREIGN KEY (logID) REFERENCES AuditLog(logID)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
