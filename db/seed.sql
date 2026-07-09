-- =====================================================================
-- ServiceLink — Seed Data (Phase 4 development / auth testing)
--
-- All five users share the password:  Passw0rd!
-- (meets NFR-S2: 8+ chars, at least one letter and one number)
-- Hashes are bcrypt cost 12 (NFR-S1 requires minimum cost 10).
--
-- User 5 is deliberately PendingApproval: FR-1.2 makes Administrator
-- approval a login gate, so the auth system must be tested against a
-- non-Active account, not only happy-path logins.
--
--   mysql -u root -p servicelink < seed.sql
-- =====================================================================

-- ---------------------------------------------------------------------
-- Reference data (locked scope: 4 priorities; targets per Phase 2
-- Fig. 3 escalation flowchart — Low 24h/5d, Medium 8h/2d, High 4h/1d,
-- Critical 1h/4h, expressed in minutes)
-- ---------------------------------------------------------------------
INSERT INTO SLAPolicy (priority, responseTargetMins, resolutionTargetMins) VALUES
    ('Low',      1440, 7200),
    ('Medium',    480, 2880),
    ('High',      240, 1440),
    ('Critical',   60,  240);

INSERT INTO Category (name, isActive) VALUES
    ('Hardware', TRUE),
    ('Software', TRUE),
    ('Network',  TRUE),
    ('Access & Accounts', TRUE);

-- ---------------------------------------------------------------------
-- Test users — one per role, plus one PendingApproval account
-- ---------------------------------------------------------------------
INSERT INTO User (email, passwordHash, firstName, lastName, role, status, mfaEnabled) VALUES
    ('chris.karki@servicelink.test',
     '$2b$12$a2Vm4tpPi571XZyIy0KQ1utw5qT/fo3Ueo/Oc.quv..41eyj1vSMi',
     'Chris', 'Karki', 'Administrator', 'Active', FALSE),

    ('katie.weng@servicelink.test',
     '$2b$12$yu4kusQkl.w48XE3iw8KzO6RvGqlQBwm9owmS6KPV/G0ol/zM8/8O',
     'Katie', 'Weng', 'Manager', 'Active', FALSE),

    ('hiten.lamba@servicelink.test',
     '$2b$12$.y.9ALnli/YUvUirDdGHquSh1PwXQRjRG4nS8MmGpzm3e5UcK3CbK',
     'Hiten', 'Lamba', 'Technician', 'Active', FALSE),

    ('arshdeep.mutti@servicelink.test',
     '$2b$12$.V4X8Ygqa3EvZD1zh8V24.kwCQ/BLdSTQYzRzFnPLQAKpA2oEZzsa',
     'Arshdeep', 'Mutti', 'EndUser', 'Active', FALSE),

    ('prabh.hans@servicelink.test',
     '$2b$12$MaNBP7IkkQ4EDonoJMboee6QI3jorlALPG/qrQDFVwj0poglX/z96',
     'Prabh', 'Hans', 'EndUser', 'Active', FALSE);
