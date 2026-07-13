-- =====================================================================
-- ServiceLink — Demo Seed Data (C0.3)
--
-- PREREQUISITE: schema.sql then seed.sql must already be loaded.
-- This file is SEPARATE from seed.sql on purpose: seed.sql is the
-- canonical auth-test fixture (Katie's automation asserts against those
-- five accounts); this file is disposable demo data for monitoring
-- sessions and development.
--
--   mysql -u root -p servicelink < db/seed_demo.sql
--
-- Adds:
--   4 users  (2 more ACTIVE Technicians — round-robin (FR-2.3 / P1.2)
--             is untestable with a single technician — and 2 End Users)
--   10 resources   (all 3 types, all 4 statuses — UC-03 EX-2 needs
--                   Disposed / LostMissing rows)
--   3 KB articles  (Published/Public, Published/Internal,
--                   PendingApproval — H2.2 approval demo) + tags
--   15 tickets     (all 6 states, all 4 priorities, 2 SLA-breached —
--                   P2.2 acceptance needs a breached row; 1 resolved
--                   ticket references a KB article)
--   8 comments     (Internal + Public mix — FR-2.4)
--   3 ticket-resource links (FR-3.2)
--   6 audit entries + field-level changes (dashboard activity feed)
--
-- Explicit primary keys throughout so FK references are deterministic.
-- Tickets start at 1001 to match the INC-10xx style used in the
-- Phase 3 prototype screens. All users' password: Passw0rd!
-- =====================================================================

-- ---------------------------------------------------------------------
-- Users 6-9 (5 already exist from seed.sql)
-- ---------------------------------------------------------------------
INSERT INTO User (userID, email, passwordHash, firstName, lastName, role, status, mfaEnabled) VALUES
    (6, 'maya.chen@servicelink.test',
     '$2b$12$RoUhZOH/5ROZD43yUxp5r.F.7tcX5cWTyOqHHGFpXKzbg7UPxtLWG',
     'Maya', 'Chen', 'Technician', 'Active', FALSE),
    (7, 'omar.farouk@servicelink.test',
     '$2b$12$WgTfElkupcFYc154FEO65.a7ygz7jpcuH9bB5.qZZ1jN8nDsVGWUK',
     'Omar', 'Farouk', 'Technician', 'Active', FALSE),
    (8, 'jane.doe@servicelink.test',
     '$2b$12$ix3q7wBAJA/qCgcam.treedUHlkPMmAqgBsJ4gN3YQ4kUjaY8vWCy',
     'Jane', 'Doe', 'EndUser', 'Active', FALSE),
    (9, 'sam.iyer@servicelink.test',
     '$2b$12$jDDpv4fKqpjdCsYe.vMpoOhhZflXhyIubuEJtJvw7M1WvG5/Hv1Re',
     'Sam', 'Iyer', 'EndUser', 'Active', FALSE);

-- Category IDs from seed.sql: 1=Hardware, 2=Software, 3=Network, 4=Access & Accounts
-- Active Technicians for round-robin: 3=Hiten, 6=Maya, 7=Omar

-- ---------------------------------------------------------------------
-- Resources (types: Hardware/Software/Virtual; statuses incl. edge cases)
-- ---------------------------------------------------------------------
INSERT INTO Resource (resourceID, resourceTag, type, make, model, serialNumber,
                      assignedUserID, status, location, purchaseDate, warrantyEndDate) VALUES
    (1, 'LT-0114', 'Hardware', 'Dell',      'XPS 15 9530',        'DX9530-88412', 4, 'InUse',      'Surrey HQ - Floor 2',  '2025-03-14', '2028-03-14'),
    (2, 'LT-0117', 'Hardware', 'Lenovo',    'ThinkPad T14 Gen 4', 'LT14-55201',   8, 'InUse',      'Surrey HQ - Floor 2',  '2025-06-02', '2028-06-02'),
    (3, 'MN-0042', 'Hardware', 'LG',        '27UK850-W',          'LG27-77120',   9, 'InUse',      'Surrey HQ - Floor 1',  '2024-11-20', '2027-11-20'),
    (4, 'PR-0007', 'Hardware', 'HP',        'LaserJet Pro M404n', 'HPLJ-40311',   NULL, 'InUse',   'Surrey HQ - Print Room','2023-08-15', '2026-08-15'),
    (5, 'SV-0003', 'Hardware', 'Dell',      'PowerEdge R750',     'PER750-00291', NULL, 'InUse',   'Server Room A',        '2024-01-30', '2029-01-30'),
    (6, 'SW-0201', 'Software', 'Adobe',     'Creative Cloud (25 seats)', NULL,    NULL, 'InUse',   'Site License',         '2026-01-01', '2026-12-31'),
    (7, 'SW-0208', 'Software', 'JetBrains', 'All Products Pack (10 seats)', NULL, NULL, 'InStock', 'Site License',         '2026-02-15', '2027-02-15'),
    (8, 'VM-0031', 'Virtual',  'Oracle',    'OCI VM.Standard.A1 (Build)', 'i-08a9f2429bc0182', 3, 'InUse', 'OCI ca-toronto-1', '2025-10-12', NULL),
    (9, 'LT-0092', 'Hardware', 'Dell',      'Latitude 5520',      'DL5520-33107', NULL, 'Disposed', 'E-waste (recycled)',  '2021-04-10', '2024-04-10'),
    (10,'LT-0101', 'Hardware', 'Apple',     'MacBook Pro 14 M2',  'MBP14-90233',  NULL, 'LostMissing', 'Last seen: Floor 3','2023-02-01', '2026-02-01');

-- ---------------------------------------------------------------------
-- KB articles (author = Technician, approver = Manager Katie [2], FR-4.1)
-- ---------------------------------------------------------------------
INSERT INTO KBArticle (articleID, title, body, authorID, approvedByID, status, visibility, createdAt, publishedAt) VALUES
    (1, 'Troubleshooting Remote VPN Connectivity Drops',
     'This guide addresses frequent VPN disconnects on Windows 11 machines using split-tunneling over home ISPs.\n\n1. Verify the client version (v4.2.1 or higher).\n2. Disable IPv6 on the tunnel adapter.\n3. If drops persist, collect logs and attach them to your ticket.',
     3, 2, 'Published', 'Public',   NOW() - INTERVAL 21 DAY, NOW() - INTERVAL 20 DAY),
    (2, 'Printer Queue Reset Procedure (HP M404n)',
     'Internal runbook: stop the Spooler service, clear C:\\Windows\\System32\\spool\\PRINTERS, restart Spooler, then power-cycle the device. Escalate to Facilities if the jam sensor stays lit.',
     6, 2, 'Published', 'Internal', NOW() - INTERVAL 14 DAY, NOW() - INTERVAL 13 DAY),
    (3, 'Requesting Additional Software License Seats',
     'DRAFT: process for requesting seat increases on site-licensed software, including approval chain and budget codes.',
     7, NULL, 'PendingApproval', 'Internal', NOW() - INTERVAL 2 DAY, NULL);

INSERT INTO ArticleTag (articleID, tag) VALUES
    (1, 'vpn'), (1, 'networking'), (1, 'windows11'),
    (2, 'printer'), (2, 'spooler'),
    (3, 'licensing'), (3, 'software');

-- ---------------------------------------------------------------------
-- Tickets 1001-1015 (createdAt spread over the last 10 days so the
-- 7-day volume chart has data whenever this file is loaded)
-- Submitters: 4=Arshdeep, 8=Jane, 9=Sam · Assignees: 3=Hiten, 6=Maya, 7=Omar
-- ---------------------------------------------------------------------
INSERT INTO Ticket (ticketID, title, description, categoryID, priority, status,
                    submittedByUserID, assignedToUserID, createdAt, resolvedAt,
                    slaBreached, resolutionSummary, linkedKBArticleID) VALUES
    -- New / unassigned queue (UC-01 AF-1)
    (1001, 'Request Adobe Creative Cloud license',
     'Need a Creative Cloud seat for the marketing contractor starting Monday.',
     2, 'Low', 'New', 8, NULL, NOW() - INTERVAL 5 HOUR, NULL, FALSE, NULL, NULL),
    (1002, 'New monitor setup for engineering desk 2-14',
     'Second monitor requested for pair-review work.',
     1, 'Low', 'New', 9, NULL, NOW() - INTERVAL 1 DAY, NULL, FALSE, NULL, NULL),
    (1003, 'Increase mailbox storage quota',
     'Mailbox at 98% capacity, cannot receive attachments from clients.',
     4, 'Medium', 'New', 4, NULL, NOW() - INTERVAL 3 HOUR, NULL, FALSE, NULL, NULL),

    -- Assigned
    (1004, 'Laptop battery drains within 2 hours',
     'ThinkPad T14 battery life dropped sharply after the latest firmware update.',
     1, 'Medium', 'Assigned', 8, 3, NOW() - INTERVAL 2 DAY, NULL, FALSE, NULL, NULL),
    (1005, 'Cannot access shared finance drive',
     'Permission denied on \\\\fileserver\\finance since this morning.',
     4, 'High', 'Assigned', 4, 6, NOW() - INTERVAL 7 HOUR, NULL, FALSE, NULL, NULL),
    (1006, 'Software update request: JetBrains 2026.2',
     'IDE prompts for the 2026.2 update; needs admin elevation to install.',
     2, 'Low', 'Assigned', 9, 7, NOW() - INTERVAL 1 DAY - INTERVAL 4 HOUR, NULL, FALSE, NULL, NULL),

    -- In Progress (1007 is the SLA-breached Critical for the demo)
    (1007, 'Production build pipeline failing',
     'CI builds on the OCI runner fail at the artifact upload step; release blocked.',
     3, 'Critical', 'InProgress', 4, 3, NOW() - INTERVAL 9 HOUR, NULL, TRUE, NULL, NULL),
    (1008, 'VPN connection dropping every 20 minutes',
     'Remote session drops repeatedly when on home Wi-Fi; wired connection unaffected.',
     3, 'Medium', 'InProgress', 8, 6, NOW() - INTERVAL 2 DAY - INTERVAL 3 HOUR, NULL, FALSE, NULL, NULL),
    (1009, 'Email sync issue on mobile',
     'Outlook mobile stopped syncing after the MFA re-enrolment.',
     4, 'Medium', 'InProgress', 9, 7, NOW() - INTERVAL 3 DAY, NULL, FALSE, NULL, NULL),
    (1010, 'Printer offline in HR suite',
     'HP M404n shows a red error light and rejects all jobs.',
     1, 'High', 'InProgress', 4, 6, NOW() - INTERVAL 6 HOUR, NULL, FALSE, NULL, NULL),

    -- Waiting on User (SLA timer pauses here per UC-02 AF-2)
    (1011, 'Access request: GitHub organization',
     'Need write access to the internal GitHub org for the new repo.',
     4, 'Low', 'WaitingOnUser', 9, 3, NOW() - INTERVAL 4 DAY, NULL, FALSE, NULL, NULL),

    -- Resolved (1012 links the VPN KB article; 1013 breached before resolution)
    (1012, 'Frequent VPN drops on Windows 11 laptop',
     'Same symptom as other remote staff: split-tunnel sessions drop on home ISP.',
     3, 'Medium', 'Resolved', 8, 3, NOW() - INTERVAL 5 DAY, NOW() - INTERVAL 4 DAY,
     FALSE, 'Client upgraded to v4.2.1 and IPv6 disabled on the tunnel adapter per KB-1.', 1),
    (1013, 'Database read replica lag alerts',
     'Replica lag exceeded 30s repeatedly during the nightly batch window.',
     3, 'Critical', 'Resolved', 4, 7, NOW() - INTERVAL 6 DAY, NOW() - INTERVAL 5 DAY,
     TRUE, 'Rebuilt the replica and retuned the batch job schedule; lag under 2s since.', NULL),
    (1014, 'Slack notifications delayed',
     'Channel notifications arriving 10+ minutes late on desktop client.',
     2, 'Low', 'Resolved', 9, 6, NOW() - INTERVAL 7 DAY, NOW() - INTERVAL 6 DAY,
     FALSE, 'Cleared desktop client cache and re-authenticated; delays gone.', NULL),

    -- Closed (past the 7-day reopen window — FR-2.2 reopen test data)
    (1015, 'Provision OCI VM for QA environment',
     'Standing up an ARM VM for the QA test bed.',
     3, 'Medium', 'Closed', 4, 3, NOW() - INTERVAL 10 DAY, NOW() - INTERVAL 9 DAY,
     FALSE, 'VM provisioned, hardened, and handed to QA with access documented.', NULL);

-- ---------------------------------------------------------------------
-- Comments (Internal = staff-only, Public = visible to submitter, FR-2.4)
-- ---------------------------------------------------------------------
INSERT INTO TicketComment (commentID, ticketID, authorUserID, commentType, bodyText, createdAt) VALUES
    (1, 1007, 3, 'Internal', 'Artifact upload fails with a 403 from the object store; suspect the bucket token expired. Rotating now.', NOW() - INTERVAL 8 HOUR),
    (2, 1007, 3, 'Public',   'We have identified the cause and are working on a fix. Builds should resume within the hour.',           NOW() - INTERVAL 7 HOUR),
    (3, 1008, 6, 'Public',   'Can you confirm your VPN client version? Settings > About. KB article "Troubleshooting Remote VPN Connectivity Drops" may help meanwhile.', NOW() - INTERVAL 1 DAY),
    (4, 1010, 6, 'Internal', 'Jam sensor lit. Following the printer queue reset runbook (KB-2) before calling Facilities.',            NOW() - INTERVAL 5 HOUR),
    (5, 1011, 3, 'Public',   'Waiting on your manager''s approval name to grant org write access — please reply here.',               NOW() - INTERVAL 3 DAY),
    (6, 1012, 3, 'Public',   'Resolved: client upgraded and IPv6 disabled on the tunnel adapter. Reply within 7 days to reopen if it recurs.', NOW() - INTERVAL 4 DAY),
    (7, 1013, 7, 'Internal', 'Replica rebuild finished 02:10; monitoring lag for one more batch cycle before resolving.',              NOW() - INTERVAL 5 DAY - INTERVAL 6 HOUR),
    (8, 1005, 6, 'Public',   'Your AD group membership looks wrong after the re-org; correcting it now.',                              NOW() - INTERVAL 5 HOUR);

-- ---------------------------------------------------------------------
-- Ticket <-> Resource links (FR-3.2; linkedBy = a Technician)
-- ---------------------------------------------------------------------
INSERT INTO TicketResource (ticketID, resourceID, linkedByUserID, linkedAt) VALUES
    (1004, 2, 3, NOW() - INTERVAL 2 DAY),        -- battery ticket -> Jane's ThinkPad
    (1007, 8, 3, NOW() - INTERVAL 8 HOUR),       -- pipeline ticket -> build VM
    (1010, 4, 6, NOW() - INTERVAL 5 HOUR);       -- printer ticket -> HP M404n

-- ---------------------------------------------------------------------
-- Audit trail (FR-6.2) — feeds the dashboard activity feed (C3.1)
-- ---------------------------------------------------------------------
INSERT INTO AuditLog (logID, actorID, entityType, entityID, action, timestamp, ipAddress) VALUES
    (1, 4, 'Ticket',         1007, 'Create', NOW() - INTERVAL 9 HOUR,  '10.0.2.15'),
    (2, 3, 'Ticket',         1007, 'Update', NOW() - INTERVAL 8 HOUR,  '10.0.2.31'),
    (3, 3, 'TicketResource', 1007, 'Link',   NOW() - INTERVAL 8 HOUR,  '10.0.2.31'),
    (4, 3, 'Ticket',         1012, 'Update', NOW() - INTERVAL 4 DAY,   '10.0.2.31'),
    (5, 2, 'KBArticle',         2, 'Update', NOW() - INTERVAL 13 DAY,  '10.0.2.8'),
    (6, 6, 'Resource',         10, 'Update', NOW() - INTERVAL 3 DAY,   '10.0.2.44');

INSERT INTO AuditLogChange (logID, fieldName, oldValue, newValue) VALUES
    (2, 'status',           'Assigned',  'InProgress'),
    (4, 'status',           'InProgress','Resolved'),
    (4, 'linkedKBArticleID', NULL,       '1'),
    (5, 'status',           'PendingApproval', 'Published'),
    (6, 'status',           'InUse',     'LostMissing'),
    (6, 'location',         'Surrey HQ - Floor 3', 'Last seen: Floor 3');
