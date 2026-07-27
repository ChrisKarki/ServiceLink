-- 007_groups.sql — Technician groups, category routing, ticket group column.
--
-- PRE-FLIGHT (schema drift is our #1 failure mode — run these FIRST):
--   SHOW CREATE TABLE User;      -- confirm userID is int(10) unsigned
--   SHOW CREATE TABLE Category;  -- confirm categoryID is int(10) unsigned
--   SHOW CREATE TABLE Ticket;    -- confirm assignedToUserID column name
--   SHOW CREATE TABLE AuditLog;  -- confirm the entityType ENUM below matches
--                                   the deployed list before the MODIFY runs.
-- Every referencing column here is INT UNSIGNED to match int(10) unsigned
-- PKs (errno 150 lesson). If any PK differs, adjust before applying.

CREATE TABLE TechGroup (
    groupID   INT UNSIGNED NOT NULL AUTO_INCREMENT,
    name      VARCHAR(80)  NOT NULL,
    isActive  TINYINT(1)   NOT NULL DEFAULT 1,
    createdAt DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (groupID),
    UNIQUE KEY uq_techgroup_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE TechGroupMember (
    groupID INT UNSIGNED NOT NULL,
    userID  INT UNSIGNED NOT NULL,
    addedAt DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (groupID, userID),
    CONSTRAINT fk_tgm_group FOREIGN KEY (groupID)
        REFERENCES TechGroup (groupID) ON DELETE CASCADE,
    CONSTRAINT fk_tgm_user  FOREIGN KEY (userID)
        REFERENCES User (userID) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- A group can serve many categories; a category can be served by many
-- groups (round-robin at the group level, then at the technician level).
CREATE TABLE CategoryGroup (
    categoryID INT UNSIGNED NOT NULL,
    groupID    INT UNSIGNED NOT NULL,
    PRIMARY KEY (categoryID, groupID),
    CONSTRAINT fk_cg_category FOREIGN KEY (categoryID)
        REFERENCES Category (categoryID) ON DELETE CASCADE,
    CONSTRAINT fk_cg_group    FOREIGN KEY (groupID)
        REFERENCES TechGroup (groupID) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Which group currently owns the ticket. Drives group-level round-robin
-- history and scopes escalation notifications to that group's managers.
ALTER TABLE Ticket
    ADD COLUMN assignedGroupID INT UNSIGNED NULL AFTER assignedToUserID,
    ADD CONSTRAINT fk_ticket_group FOREIGN KEY (assignedGroupID)
        REFERENCES TechGroup (groupID);

-- Keep the DB ENUM and services/audit.py ENTITY_TYPES in lock-step
-- (both are updated in this change set). Verify against SHOW CREATE TABLE
-- AuditLog output first — this list must be the deployed list + TechGroup.
ALTER TABLE AuditLog
    MODIFY entityType ENUM('Ticket','Resource','User','KBArticle',
                           'TicketComment','TicketResource','Category',
                           'SLAPolicy','SystemSetting','TicketAttachment',
                           'TechGroup') NOT NULL;
