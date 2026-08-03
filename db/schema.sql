-- phpMyAdmin SQL Dump
-- version 5.2.3
-- https://www.phpmyadmin.net/
--
-- Host: localhost
-- Generation Time: Aug 03, 2026 at 12:02 AM
-- Server version: 10.11.18-MariaDB
-- PHP Version: 8.3.31

SET SQL_MODE = "NO_AUTO_VALUE_ON_ZERO";
START TRANSACTION;
SET time_zone = "+00:00";


/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!40101 SET NAMES utf8mb4 */;

--
-- Database: `servicelink`
--
CREATE DATABASE IF NOT EXISTS `servicelink` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE `servicelink`;

-- --------------------------------------------------------

--
-- Table structure for table `ArticleTag`
--

CREATE TABLE `ArticleTag` (
  `articleID` int(10) UNSIGNED NOT NULL,
  `tag` varchar(40) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Table structure for table `Attachment`
--

CREATE TABLE `Attachment` (
  `attachmentID` int(10) UNSIGNED NOT NULL,
  `ticketID` int(10) UNSIGNED NOT NULL,
  `fileName` varchar(255) NOT NULL,
  `fileType` varchar(100) NOT NULL,
  `fileSizeBytes` int(10) UNSIGNED NOT NULL,
  `uploadedAt` datetime NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Table structure for table `AuditLog`
--

CREATE TABLE `AuditLog` (
  `logID` int(10) UNSIGNED NOT NULL,
  `actorID` int(10) UNSIGNED NOT NULL,
  `entityType` enum('Ticket','Resource','User','KBArticle','TicketComment','TicketResource','Category','SLAPolicy','SystemSetting','TicketAttachment','TechGroup') NOT NULL,
  `entityID` int(10) UNSIGNED NOT NULL,
  `action` enum('Create','Update','Delete','Link','Unlink') NOT NULL,
  `timestamp` datetime NOT NULL DEFAULT current_timestamp(),
  `ipAddress` varchar(45) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Table structure for table `AuditLogChange`
--

CREATE TABLE `AuditLogChange` (
  `logID` int(10) UNSIGNED NOT NULL,
  `fieldName` varchar(64) NOT NULL,
  `oldValue` text DEFAULT NULL,
  `newValue` text DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Table structure for table `Category`
--

CREATE TABLE `Category` (
  `categoryID` int(10) UNSIGNED NOT NULL,
  `name` varchar(80) NOT NULL,
  `isActive` tinyint(1) NOT NULL DEFAULT 1
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Table structure for table `CategoryGroup`
--

CREATE TABLE `CategoryGroup` (
  `categoryID` int(10) UNSIGNED NOT NULL,
  `groupID` int(10) UNSIGNED NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `KBArticle`
--

CREATE TABLE `KBArticle` (
  `articleID` int(10) UNSIGNED NOT NULL,
  `title` varchar(150) NOT NULL,
  `body` mediumtext NOT NULL,
  `authorID` int(10) UNSIGNED NOT NULL,
  `approvedByID` int(10) UNSIGNED DEFAULT NULL,
  `status` enum('Draft','PendingApproval','Published','Archived') NOT NULL DEFAULT 'Draft',
  `visibility` enum('Internal','Public') NOT NULL DEFAULT 'Internal',
  `createdAt` datetime NOT NULL DEFAULT current_timestamp(),
  `publishedAt` datetime DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Table structure for table `KBArticleTag`
--

CREATE TABLE `KBArticleTag` (
  `articleID` int(10) UNSIGNED NOT NULL,
  `tag` varchar(40) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Table structure for table `Notification`
--

CREATE TABLE `Notification` (
  `notificationID` int(10) UNSIGNED NOT NULL,
  `userID` int(10) UNSIGNED NOT NULL,
  `subject` varchar(255) NOT NULL,
  `body` text NOT NULL,
  `link` varchar(255) DEFAULT NULL,
  `readAt` datetime DEFAULT NULL,
  `createdAt` datetime NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Table structure for table `PasswordResetOTP`
--

CREATE TABLE `PasswordResetOTP` (
  `otpID` int(10) UNSIGNED NOT NULL,
  `userID` int(10) UNSIGNED NOT NULL,
  `codeHash` char(60) NOT NULL,
  `expiresAt` datetime NOT NULL,
  `usedAt` datetime DEFAULT NULL,
  `attempts` tinyint(3) UNSIGNED NOT NULL DEFAULT 0,
  `createdAt` datetime NOT NULL DEFAULT current_timestamp(),
  `requestIP` varchar(45) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Table structure for table `Resource`
--

CREATE TABLE `Resource` (
  `resourceID` int(10) UNSIGNED NOT NULL,
  `resourceTag` varchar(40) NOT NULL,
  `type` enum('Hardware','Software','Virtual') NOT NULL,
  `make` varchar(60) NOT NULL,
  `model` varchar(60) NOT NULL,
  `serialNumber` varchar(80) DEFAULT NULL,
  `assignedUserID` int(10) UNSIGNED DEFAULT NULL,
  `status` enum('InUse','InStock','Disposed','LostMissing') NOT NULL,
  `location` varchar(120) NOT NULL,
  `purchaseDate` date DEFAULT NULL,
  `warrantyEndDate` date DEFAULT NULL,
  `createdAt` datetime NOT NULL DEFAULT current_timestamp(),
  `updatedAt` datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp()
) ;

-- --------------------------------------------------------

--
-- Table structure for table `SLAPolicy`
--

CREATE TABLE `SLAPolicy` (
  `priority` enum('Low','Medium','High','Critical') NOT NULL,
  `responseTargetMins` int(10) UNSIGNED NOT NULL,
  `resolutionTargetMins` int(10) UNSIGNED NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Table structure for table `SystemSetting`
--

CREATE TABLE `SystemSetting` (
  `settingKey` varchar(64) NOT NULL,
  `settingValue` varchar(255) NOT NULL,
  `updatedByID` int(10) UNSIGNED DEFAULT NULL,
  `updatedAt` datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Table structure for table `TechGroup`
--

CREATE TABLE `TechGroup` (
  `groupID` int(10) UNSIGNED NOT NULL,
  `name` varchar(80) NOT NULL,
  `isActive` tinyint(1) NOT NULL DEFAULT 1,
  `createdAt` datetime NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `TechGroupMember`
--

CREATE TABLE `TechGroupMember` (
  `groupID` int(10) UNSIGNED NOT NULL,
  `userID` int(10) UNSIGNED NOT NULL,
  `addedAt` datetime NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `Ticket`
--

CREATE TABLE `Ticket` (
  `ticketID` int(10) UNSIGNED NOT NULL,
  `title` varchar(150) NOT NULL,
  `description` text NOT NULL,
  `categoryID` int(10) UNSIGNED NOT NULL,
  `priority` enum('Low','Medium','High','Critical') NOT NULL,
  `status` enum('New','Assigned','InProgress','WaitingOnUser','Resolved','Closed') NOT NULL DEFAULT 'New',
  `submittedByUserID` int(10) UNSIGNED NOT NULL,
  `assignedToUserID` int(10) UNSIGNED DEFAULT NULL,
  `assignedGroupID` int(10) UNSIGNED DEFAULT NULL,
  `createdAt` datetime NOT NULL DEFAULT current_timestamp(),
  `resolvedAt` datetime DEFAULT NULL,
  `slaBreached` tinyint(1) NOT NULL DEFAULT 0,
  `slaPausedMins` int(10) UNSIGNED NOT NULL DEFAULT 0,
  `slaPausedAt` datetime DEFAULT NULL,
  `resolutionSummary` text DEFAULT NULL,
  `linkedKBArticleID` int(10) UNSIGNED DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Table structure for table `TicketAttachment`
--

CREATE TABLE `TicketAttachment` (
  `attachmentID` int(10) UNSIGNED NOT NULL,
  `ticketID` int(10) UNSIGNED NOT NULL,
  `originalName` varchar(255) NOT NULL,
  `storedName` varchar(80) NOT NULL,
  `mimeType` varchar(100) NOT NULL,
  `sizeBytes` int(10) UNSIGNED NOT NULL,
  `uploadedByID` int(10) UNSIGNED NOT NULL,
  `uploadedAt` datetime NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Table structure for table `TicketComment`
--

CREATE TABLE `TicketComment` (
  `commentID` int(10) UNSIGNED NOT NULL,
  `ticketID` int(10) UNSIGNED NOT NULL,
  `authorUserID` int(10) UNSIGNED NOT NULL,
  `commentType` enum('Internal','Public') NOT NULL,
  `bodyText` text NOT NULL,
  `createdAt` datetime NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Table structure for table `TicketDraft`
--

CREATE TABLE `TicketDraft` (
  `draftID` int(10) UNSIGNED NOT NULL,
  `userID` int(10) UNSIGNED NOT NULL,
  `title` varchar(150) DEFAULT NULL,
  `description` text DEFAULT NULL,
  `categoryID` int(10) UNSIGNED DEFAULT NULL,
  `priority` varchar(20) DEFAULT NULL,
  `createdAt` datetime NOT NULL DEFAULT current_timestamp(),
  `updatedAt` datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `TicketResource`
--

CREATE TABLE `TicketResource` (
  `ticketID` int(10) UNSIGNED NOT NULL,
  `resourceID` int(10) UNSIGNED NOT NULL,
  `linkedByUserID` int(10) UNSIGNED NOT NULL,
  `linkedAt` datetime NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Table structure for table `User`
--

CREATE TABLE `User` (
  `userID` int(10) UNSIGNED NOT NULL,
  `email` varchar(254) NOT NULL,
  `passwordHash` char(60) NOT NULL,
  `firstName` varchar(50) NOT NULL,
  `lastName` varchar(50) NOT NULL,
  `role` enum('EndUser','Technician','Manager','Administrator') NOT NULL,
  `status` enum('PendingApproval','Active','Suspended') NOT NULL DEFAULT 'PendingApproval',
  `mfaEnabled` tinyint(1) NOT NULL DEFAULT 0,
  `totpSecret` varchar(64) DEFAULT NULL,
  `totpEnrolledAt` datetime DEFAULT NULL,
  `createdAt` datetime NOT NULL DEFAULT current_timestamp(),
  `lastLoginAt` datetime DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- Indexes for dumped tables
--

--
-- Indexes for table `ArticleTag`
--
ALTER TABLE `ArticleTag`
  ADD PRIMARY KEY (`articleID`,`tag`);

--
-- Indexes for table `Attachment`
--
ALTER TABLE `Attachment`
  ADD PRIMARY KEY (`attachmentID`),
  ADD KEY `ticketID` (`ticketID`);

--
-- Indexes for table `AuditLog`
--
ALTER TABLE `AuditLog`
  ADD PRIMARY KEY (`logID`),
  ADD KEY `actorID` (`actorID`);

--
-- Indexes for table `AuditLogChange`
--
ALTER TABLE `AuditLogChange`
  ADD PRIMARY KEY (`logID`,`fieldName`);

--
-- Indexes for table `Category`
--
ALTER TABLE `Category`
  ADD PRIMARY KEY (`categoryID`),
  ADD UNIQUE KEY `name` (`name`);

--
-- Indexes for table `CategoryGroup`
--
ALTER TABLE `CategoryGroup`
  ADD PRIMARY KEY (`categoryID`,`groupID`),
  ADD KEY `fk_cg_group` (`groupID`);

--
-- Indexes for table `KBArticle`
--
ALTER TABLE `KBArticle`
  ADD PRIMARY KEY (`articleID`),
  ADD KEY `authorID` (`authorID`),
  ADD KEY `approvedByID` (`approvedByID`);

--
-- Indexes for table `KBArticleTag`
--
ALTER TABLE `KBArticleTag`
  ADD PRIMARY KEY (`articleID`,`tag`),
  ADD KEY `idx_kbtag_tag` (`tag`);

--
-- Indexes for table `Notification`
--
ALTER TABLE `Notification`
  ADD PRIMARY KEY (`notificationID`),
  ADD KEY `idx_notif_user` (`userID`,`readAt`,`createdAt`);

--
-- Indexes for table `PasswordResetOTP`
--
ALTER TABLE `PasswordResetOTP`
  ADD PRIMARY KEY (`otpID`),
  ADD KEY `idx_otp_lookup` (`userID`,`usedAt`,`expiresAt`);

--
-- Indexes for table `Resource`
--
ALTER TABLE `Resource`
  ADD PRIMARY KEY (`resourceID`),
  ADD UNIQUE KEY `resourceTag` (`resourceTag`),
  ADD KEY `assignedUserID` (`assignedUserID`);

--
-- Indexes for table `SLAPolicy`
--
ALTER TABLE `SLAPolicy`
  ADD PRIMARY KEY (`priority`);

--
-- Indexes for table `SystemSetting`
--
ALTER TABLE `SystemSetting`
  ADD PRIMARY KEY (`settingKey`),
  ADD KEY `fk_setting_user` (`updatedByID`);

--
-- Indexes for table `TechGroup`
--
ALTER TABLE `TechGroup`
  ADD PRIMARY KEY (`groupID`),
  ADD UNIQUE KEY `uq_techgroup_name` (`name`);

--
-- Indexes for table `TechGroupMember`
--
ALTER TABLE `TechGroupMember`
  ADD PRIMARY KEY (`groupID`,`userID`),
  ADD KEY `fk_tgm_user` (`userID`);

--
-- Indexes for table `Ticket`
--
ALTER TABLE `Ticket`
  ADD PRIMARY KEY (`ticketID`),
  ADD KEY `categoryID` (`categoryID`),
  ADD KEY `priority` (`priority`),
  ADD KEY `submittedByUserID` (`submittedByUserID`),
  ADD KEY `assignedToUserID` (`assignedToUserID`),
  ADD KEY `linkedKBArticleID` (`linkedKBArticleID`),
  ADD KEY `fk_ticket_group` (`assignedGroupID`);

--
-- Indexes for table `TicketAttachment`
--
ALTER TABLE `TicketAttachment`
  ADD PRIMARY KEY (`attachmentID`),
  ADD UNIQUE KEY `uq_stored` (`storedName`),
  ADD KEY `fk_attach_user` (`uploadedByID`),
  ADD KEY `idx_attach_ticket` (`ticketID`,`uploadedAt`);

--
-- Indexes for table `TicketComment`
--
ALTER TABLE `TicketComment`
  ADD PRIMARY KEY (`commentID`),
  ADD KEY `ticketID` (`ticketID`),
  ADD KEY `authorUserID` (`authorUserID`);

--
-- Indexes for table `TicketDraft`
--
ALTER TABLE `TicketDraft`
  ADD PRIMARY KEY (`draftID`),
  ADD KEY `idx_draft_user` (`userID`,`updatedAt`);

--
-- Indexes for table `TicketResource`
--
ALTER TABLE `TicketResource`
  ADD PRIMARY KEY (`ticketID`,`resourceID`),
  ADD KEY `resourceID` (`resourceID`),
  ADD KEY `linkedByUserID` (`linkedByUserID`);

--
-- Indexes for table `User`
--
ALTER TABLE `User`
  ADD PRIMARY KEY (`userID`),
  ADD UNIQUE KEY `email` (`email`);

--
-- AUTO_INCREMENT for dumped tables
--

--
-- AUTO_INCREMENT for table `Attachment`
--
ALTER TABLE `Attachment`
  MODIFY `attachmentID` int(10) UNSIGNED NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `AuditLog`
--
ALTER TABLE `AuditLog`
  MODIFY `logID` int(10) UNSIGNED NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `Category`
--
ALTER TABLE `Category`
  MODIFY `categoryID` int(10) UNSIGNED NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `KBArticle`
--
ALTER TABLE `KBArticle`
  MODIFY `articleID` int(10) UNSIGNED NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `Notification`
--
ALTER TABLE `Notification`
  MODIFY `notificationID` int(10) UNSIGNED NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `PasswordResetOTP`
--
ALTER TABLE `PasswordResetOTP`
  MODIFY `otpID` int(10) UNSIGNED NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `Resource`
--
ALTER TABLE `Resource`
  MODIFY `resourceID` int(10) UNSIGNED NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `TechGroup`
--
ALTER TABLE `TechGroup`
  MODIFY `groupID` int(10) UNSIGNED NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `Ticket`
--
ALTER TABLE `Ticket`
  MODIFY `ticketID` int(10) UNSIGNED NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `TicketAttachment`
--
ALTER TABLE `TicketAttachment`
  MODIFY `attachmentID` int(10) UNSIGNED NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `TicketComment`
--
ALTER TABLE `TicketComment`
  MODIFY `commentID` int(10) UNSIGNED NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `TicketDraft`
--
ALTER TABLE `TicketDraft`
  MODIFY `draftID` int(10) UNSIGNED NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `User`
--
ALTER TABLE `User`
  MODIFY `userID` int(10) UNSIGNED NOT NULL AUTO_INCREMENT;

--
-- Constraints for dumped tables
--

--
-- Constraints for table `ArticleTag`
--
ALTER TABLE `ArticleTag`
  ADD CONSTRAINT `ArticleTag_ibfk_1` FOREIGN KEY (`articleID`) REFERENCES `KBArticle` (`articleID`) ON DELETE CASCADE;

--
-- Constraints for table `Attachment`
--
ALTER TABLE `Attachment`
  ADD CONSTRAINT `Attachment_ibfk_1` FOREIGN KEY (`ticketID`) REFERENCES `Ticket` (`ticketID`) ON DELETE CASCADE;

--
-- Constraints for table `AuditLog`
--
ALTER TABLE `AuditLog`
  ADD CONSTRAINT `AuditLog_ibfk_1` FOREIGN KEY (`actorID`) REFERENCES `User` (`userID`);

--
-- Constraints for table `AuditLogChange`
--
ALTER TABLE `AuditLogChange`
  ADD CONSTRAINT `AuditLogChange_ibfk_1` FOREIGN KEY (`logID`) REFERENCES `AuditLog` (`logID`) ON DELETE CASCADE;

--
-- Constraints for table `CategoryGroup`
--
ALTER TABLE `CategoryGroup`
  ADD CONSTRAINT `fk_cg_category` FOREIGN KEY (`categoryID`) REFERENCES `Category` (`categoryID`) ON DELETE CASCADE,
  ADD CONSTRAINT `fk_cg_group` FOREIGN KEY (`groupID`) REFERENCES `TechGroup` (`groupID`) ON DELETE CASCADE;

--
-- Constraints for table `KBArticle`
--
ALTER TABLE `KBArticle`
  ADD CONSTRAINT `KBArticle_ibfk_1` FOREIGN KEY (`authorID`) REFERENCES `User` (`userID`),
  ADD CONSTRAINT `KBArticle_ibfk_2` FOREIGN KEY (`approvedByID`) REFERENCES `User` (`userID`);

--
-- Constraints for table `KBArticleTag`
--
ALTER TABLE `KBArticleTag`
  ADD CONSTRAINT `fk_kbtag_article` FOREIGN KEY (`articleID`) REFERENCES `KBArticle` (`articleID`) ON DELETE CASCADE;

--
-- Constraints for table `Notification`
--
ALTER TABLE `Notification`
  ADD CONSTRAINT `fk_notif_user` FOREIGN KEY (`userID`) REFERENCES `User` (`userID`) ON DELETE CASCADE;

--
-- Constraints for table `PasswordResetOTP`
--
ALTER TABLE `PasswordResetOTP`
  ADD CONSTRAINT `fk_otp_user` FOREIGN KEY (`userID`) REFERENCES `User` (`userID`) ON DELETE CASCADE;

--
-- Constraints for table `Resource`
--
ALTER TABLE `Resource`
  ADD CONSTRAINT `Resource_ibfk_1` FOREIGN KEY (`assignedUserID`) REFERENCES `User` (`userID`) ON DELETE SET NULL;

--
-- Constraints for table `SystemSetting`
--
ALTER TABLE `SystemSetting`
  ADD CONSTRAINT `fk_setting_user` FOREIGN KEY (`updatedByID`) REFERENCES `User` (`userID`) ON DELETE SET NULL;

--
-- Constraints for table `TechGroupMember`
--
ALTER TABLE `TechGroupMember`
  ADD CONSTRAINT `fk_tgm_group` FOREIGN KEY (`groupID`) REFERENCES `TechGroup` (`groupID`) ON DELETE CASCADE,
  ADD CONSTRAINT `fk_tgm_user` FOREIGN KEY (`userID`) REFERENCES `User` (`userID`) ON DELETE CASCADE;

--
-- Constraints for table `Ticket`
--
ALTER TABLE `Ticket`
  ADD CONSTRAINT `Ticket_ibfk_1` FOREIGN KEY (`categoryID`) REFERENCES `Category` (`categoryID`),
  ADD CONSTRAINT `Ticket_ibfk_2` FOREIGN KEY (`priority`) REFERENCES `SLAPolicy` (`priority`),
  ADD CONSTRAINT `Ticket_ibfk_3` FOREIGN KEY (`submittedByUserID`) REFERENCES `User` (`userID`),
  ADD CONSTRAINT `Ticket_ibfk_4` FOREIGN KEY (`assignedToUserID`) REFERENCES `User` (`userID`) ON DELETE SET NULL,
  ADD CONSTRAINT `Ticket_ibfk_5` FOREIGN KEY (`linkedKBArticleID`) REFERENCES `KBArticle` (`articleID`) ON DELETE SET NULL,
  ADD CONSTRAINT `fk_ticket_group` FOREIGN KEY (`assignedGroupID`) REFERENCES `TechGroup` (`groupID`);

--
-- Constraints for table `TicketAttachment`
--
ALTER TABLE `TicketAttachment`
  ADD CONSTRAINT `fk_attach_ticket` FOREIGN KEY (`ticketID`) REFERENCES `Ticket` (`ticketID`) ON DELETE CASCADE,
  ADD CONSTRAINT `fk_attach_user` FOREIGN KEY (`uploadedByID`) REFERENCES `User` (`userID`);

--
-- Constraints for table `TicketComment`
--
ALTER TABLE `TicketComment`
  ADD CONSTRAINT `TicketComment_ibfk_1` FOREIGN KEY (`ticketID`) REFERENCES `Ticket` (`ticketID`) ON DELETE CASCADE,
  ADD CONSTRAINT `TicketComment_ibfk_2` FOREIGN KEY (`authorUserID`) REFERENCES `User` (`userID`);

--
-- Constraints for table `TicketDraft`
--
ALTER TABLE `TicketDraft`
  ADD CONSTRAINT `fk_draft_user` FOREIGN KEY (`userID`) REFERENCES `User` (`userID`) ON DELETE CASCADE;

--
-- Constraints for table `TicketResource`
--
ALTER TABLE `TicketResource`
  ADD CONSTRAINT `TicketResource_ibfk_1` FOREIGN KEY (`ticketID`) REFERENCES `Ticket` (`ticketID`) ON DELETE CASCADE,
  ADD CONSTRAINT `TicketResource_ibfk_2` FOREIGN KEY (`resourceID`) REFERENCES `Resource` (`resourceID`) ON DELETE CASCADE,
  ADD CONSTRAINT `TicketResource_ibfk_3` FOREIGN KEY (`linkedByUserID`) REFERENCES `User` (`userID`);
COMMIT;

/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;

-- =====================================================================
--  ServiceLink - Database Account Provisioning
--  Group A - INFO 2413 S50
--
--  Creates the least-privilege MariaDB/MySQL account that the Flask
--  application uses to reach the database. Run this ONCE, as root,
--  after the schema, migrations and seed data have been loaded.
--
--    Windows (XAMPP):  cd C:\xampp\mysql\bin
--                      .\mysql -u root < create_db_user.sql
--
--  These credentials must match the DB_USER / DB_PASSWORD values in .env.
-- =====================================================================

-- Two host entries are created on purpose. A client connecting to
-- 127.0.0.1 may be matched against either 'localhost' or '127.0.0.1'
-- depending on whether name resolution is enabled on the server, so both
-- are defined to avoid an "access denied" that is really a host mismatch.

CREATE USER IF NOT EXISTS 'servicelink_app'@'localhost'
  IDENTIFIED BY 'ServiceLink2026DB';

CREATE USER IF NOT EXISTS 'servicelink_app'@'127.0.0.1'
  IDENTIFIED BY 'ServiceLink2026DB';

-- Re-running the script resets the password to the documented value.
ALTER USER 'servicelink_app'@'localhost'  IDENTIFIED BY 'ServiceLink2026DB';
ALTER USER 'servicelink_app'@'127.0.0.1'  IDENTIFIED BY 'ServiceLink2026DB';

-- Data manipulation only. The account cannot create, alter or drop
-- tables, so an application fault can never change the schema. Schema
-- changes ship as migration scripts and are applied by an administrator.
GRANT SELECT, INSERT, UPDATE, DELETE
  ON `servicelink`.* TO 'servicelink_app'@'localhost';

GRANT SELECT, INSERT, UPDATE, DELETE
  ON `servicelink`.* TO 'servicelink_app'@'127.0.0.1';

FLUSH PRIVILEGES;

-- Verification: both rows should appear, and the grants should list
-- SELECT, INSERT, UPDATE, DELETE on `servicelink`.* only.
SELECT User, Host FROM mysql.user WHERE User = 'servicelink_app';
SHOW GRANTS FOR 'servicelink_app'@'127.0.0.1';

