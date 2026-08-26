-- phpMyAdmin SQL Dump
-- version 5.2.1
-- https://www.phpmyadmin.net/
--
-- Host: 127.0.0.1
-- Generation Time: Sep 21, 2025 at 06:21 PM
-- Server version: 10.4.32-MariaDB
-- PHP Version: 8.2.12

SET SQL_MODE = "NO_AUTO_VALUE_ON_ZERO";
START TRANSACTION;
SET time_zone = "+00:00";


/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!40101 SET NAMES utf8mb4 */;

--
-- Database: `exam_proctoring`
--

-- --------------------------------------------------------

--
-- Table structure for table `proctor_alerts`
--

CREATE TABLE `proctor_alerts` (
  `id` int(11) NOT NULL,
  `test_id` int(11) NOT NULL,
  `attempt_token` varchar(128) NOT NULL,
  `ts` datetime NOT NULL,
  `severity` enum('info','warning','critical') NOT NULL DEFAULT 'warning',
  `code` varchar(100) NOT NULL,
  `message` text DEFAULT NULL,
  `meta` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL CHECK (json_valid(`meta`)),
  `created_at` timestamp NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `proctor_recordings`
--

CREATE TABLE `proctor_recordings` (
  `id` int(11) NOT NULL,
  `test_id` int(11) NOT NULL,
  `attempt_token` varchar(128) NOT NULL,
  `path` varchar(500) NOT NULL,
  `filename` varchar(255) NOT NULL,
  `size` bigint(20) NOT NULL,
  `duration_seconds` int(11) DEFAULT NULL,
  `created_at` timestamp NOT NULL DEFAULT current_timestamp(),
  `uploaded_by` int(11) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `questions`
--

CREATE TABLE `questions` (
  `id` int(11) NOT NULL,
  `test_id` int(11) NOT NULL,
  `question_text` text NOT NULL,
  `option_a` varchar(255) NOT NULL,
  `option_b` varchar(255) NOT NULL,
  `option_c` varchar(255) NOT NULL,
  `option_d` varchar(255) NOT NULL,
  `correct_answer` enum('a','b','c','d') NOT NULL,
  `marks` int(11) DEFAULT 1,
  `created_at` timestamp NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Dumping data for table `questions`
--

INSERT INTO `questions` (`id`, `test_id`, `question_text`, `option_a`, `option_b`, `option_c`, `option_d`, `correct_answer`, `marks`, `created_at`) VALUES
(1, 1, 'jnyvtcd', 'unb', 'uhyguby', 'njuby', 'uyg', 'b', 1, '2025-09-21 11:11:52'),
(2, 2, 'What is you name ?', 'Ved', 'Vir', 'Darshan', 'Raj', 'b', 1, '2025-09-21 15:59:25'),
(3, 2, 'ytavdhgsjh', 'ascsd', 'ds', 'dssd', 'das', 'c', 1, '2025-09-21 15:59:48'),
(4, 2, 'kabdhja', 'dac', 'ads', 'dsd', 'sf', 'a', 1, '2025-09-21 16:00:29');

-- --------------------------------------------------------

--
-- Table structure for table `tests`
--

CREATE TABLE `tests` (
  `id` int(11) NOT NULL,
  `test_id` varchar(20) NOT NULL,
  `title` varchar(255) NOT NULL,
  `description` text DEFAULT NULL,
  `duration_minutes` int(11) NOT NULL,
  `total_questions` int(11) NOT NULL,
  `passing_marks` int(11) NOT NULL,
  `status` enum('draft','published') NOT NULL DEFAULT 'draft',
  `start_time` datetime DEFAULT NULL,
  `end_time` datetime DEFAULT NULL,
  `created_by` int(11) NOT NULL,
  `created_at` timestamp NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Dumping data for table `tests`
--

INSERT INTO `tests` (`id`, `test_id`, `title`, `description`, `duration_minutes`, `total_questions`, `passing_marks`, `status`, `start_time`, `end_time`, `created_by`, `created_at`) VALUES
(1, 'TEST001', 'EVS TEST', 'ajncdhbjg', 90, 50, 100, 'published', '2025-08-26 20:15:00', '2025-11-21 23:15:00', 2, '2025-08-26 14:45:17'),
(2, 'test231', 'Mcq test', 'nuybvtrcd', 5, 5, 3, 'published', '2025-09-21 21:27:00', '2025-09-22 21:28:00', 6, '2025-09-21 15:58:17');

-- --------------------------------------------------------

--
-- Table structure for table `test_eligibility`
--

CREATE TABLE `test_eligibility` (
  `id` int(11) NOT NULL,
  `test_id` int(11) NOT NULL,
  `student_id` int(11) NOT NULL,
  `assigned_at` timestamp NOT NULL DEFAULT current_timestamp(),
  `assigned_by` int(11) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Dumping data for table `test_eligibility`
--

INSERT INTO `test_eligibility` (`id`, `test_id`, `student_id`, `assigned_at`, `assigned_by`) VALUES
(3, 1, 4, '2025-09-21 12:10:42', 5),
(4, 2, 4, '2025-09-21 16:00:58', 6);

-- --------------------------------------------------------

--
-- Table structure for table `test_results`
--

CREATE TABLE `test_results` (
  `id` int(11) NOT NULL,
  `test_id` int(11) NOT NULL,
  `student_id` int(11) NOT NULL,
  `attempt_token` varchar(100) NOT NULL,
  `score` int(11) NOT NULL,
  `total_marks` int(11) NOT NULL,
  `passed` tinyint(1) NOT NULL,
  `submitted_at` timestamp NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Dumping data for table `test_results`
--

INSERT INTO `test_results` (`id`, `test_id`, `student_id`, `attempt_token`, `score`, `total_marks`, `passed`, `submitted_at`) VALUES
(1, 1, 4, '33a9796bf55b678de0474abb', 1, 1, 0, '2025-09-21 11:17:11'),
(2, 1, 4, 'cfc3ab0bce7d5421d78633c7', 0, 1, 0, '2025-09-21 11:17:24'),
(3, 1, 4, 'c0573ecd5e1326d648b0abb3', 1, 1, 0, '2025-09-21 12:19:57'),
(4, 1, 4, 'd7bab2d5a7191759b1834f95', 0, 1, 0, '2025-09-21 12:46:18'),
(5, 1, 4, '4acd99635f59ea1d977235d6', 0, 1, 0, '2025-09-21 12:55:30'),
(6, 1, 4, '3246821e8a9082e68495ce96', 0, 1, 0, '2025-09-21 13:01:18'),
(7, 1, 4, '21f4f657fe5453c3f68ac904', 0, 1, 0, '2025-09-21 13:02:11'),
(8, 1, 4, 'e231b2767c51bc0c289355bc', 0, 1, 0, '2025-09-21 13:03:29'),
(9, 1, 4, '0faf5cf0b23f689c9538f9b5', 0, 1, 0, '2025-09-21 13:26:22'),
(11, 1, 4, 'bca5c0b57d050c7978477637', 0, 1, 0, '2025-09-21 13:48:13'),
(12, 2, 4, '2cf982f0152bea1a8852cf50', 0, 3, 0, '2025-09-21 16:08:38'),
(13, 2, 4, '69ce595f5ab2d043b1018e94', 0, 3, 0, '2025-09-21 16:10:46');

-- --------------------------------------------------------

--
-- Table structure for table `test_submissions`
--

CREATE TABLE `test_submissions` (
  `id` int(11) NOT NULL,
  `test_id` int(11) NOT NULL,
  `student_id` int(11) NOT NULL,
  `question_id` int(11) NOT NULL,
  `selected_answer` enum('a','b','c','d') DEFAULT NULL,
  `answered_at` timestamp NOT NULL DEFAULT current_timestamp(),
  `attempt_token` varchar(100) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Dumping data for table `test_submissions`
--

INSERT INTO `test_submissions` (`id`, `test_id`, `student_id`, `question_id`, `selected_answer`, `answered_at`, `attempt_token`) VALUES
(1, 1, 4, 1, 'b', '2025-09-21 11:17:11', '33a9796bf55b678de0474abb'),
(2, 1, 4, 1, 'd', '2025-09-21 11:17:24', 'cfc3ab0bce7d5421d78633c7'),
(3, 1, 4, 1, 'b', '2025-09-21 12:19:57', 'c0573ecd5e1326d648b0abb3'),
(4, 1, 4, 1, 'c', '2025-09-21 12:46:18', 'd7bab2d5a7191759b1834f95'),
(5, 1, 4, 1, NULL, '2025-09-21 12:55:30', '4acd99635f59ea1d977235d6'),
(6, 1, 4, 1, 'd', '2025-09-21 13:01:18', '3246821e8a9082e68495ce96'),
(7, 1, 4, 1, 'd', '2025-09-21 13:02:11', '21f4f657fe5453c3f68ac904'),
(8, 1, 4, 1, 'd', '2025-09-21 13:03:29', 'e231b2767c51bc0c289355bc'),
(9, 1, 4, 1, 'c', '2025-09-21 13:26:22', '0faf5cf0b23f689c9538f9b5'),
(11, 1, 4, 1, NULL, '2025-09-21 13:48:13', 'bca5c0b57d050c7978477637'),
(12, 2, 4, 2, 'a', '2025-09-21 16:08:38', '2cf982f0152bea1a8852cf50'),
(13, 2, 4, 3, 'b', '2025-09-21 16:08:38', '2cf982f0152bea1a8852cf50'),
(14, 2, 4, 4, 'b', '2025-09-21 16:08:38', '2cf982f0152bea1a8852cf50'),
(15, 2, 4, 2, 'a', '2025-09-21 16:10:46', '69ce595f5ab2d043b1018e94'),
(16, 2, 4, 3, 'a', '2025-09-21 16:10:46', '69ce595f5ab2d043b1018e94'),
(17, 2, 4, 4, 'b', '2025-09-21 16:10:46', '69ce595f5ab2d043b1018e94');

-- --------------------------------------------------------

--
-- Table structure for table `users`
--

CREATE TABLE `users` (
  `id` int(11) NOT NULL,
  `user_id` varchar(20) NOT NULL,
  `name` varchar(100) NOT NULL,
  `email` varchar(100) NOT NULL,
  `password` varchar(255) NOT NULL,
  `role` enum('student','admin') NOT NULL DEFAULT 'student',
  `status` enum('active','inactive') NOT NULL DEFAULT 'active',
  `created_at` timestamp NOT NULL DEFAULT current_timestamp(),
  `phone` varchar(20) DEFAULT NULL,
  `bio` text DEFAULT NULL,
  `avatar` varchar(255) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Dumping data for table `users`
--

INSERT INTO `users` (`id`, `user_id`, `name`, `email`, `password`, `role`, `status`, `created_at`, `phone`, `bio`, `avatar`) VALUES
(1, 'ADM001', 'Admin User', 'admin@test.com', '$2y$10$92IXUNpkjO0rOQ5byMi.Ye4oKoEa3Ro9llC/.og/at2.uheWG/igi', 'admin', 'active', '2025-08-26 14:31:51', NULL, NULL, NULL),
(2, 'STU001', 'ved gunjal', 'ved@gmail.com', '$2y$10$joIB9/BOMXEf1bGBiAK0lOImzgODayIWZH4fOEljtkO44nUmVFg9q', 'admin', 'active', '2025-08-26 14:36:50', NULL, NULL, NULL),
(3, 'STU002', 'Rajesh khanna', 'rajesh@gmail.com', '$2y$10$nirODU3yME/iH62EpacfPO57fuSanQcDUZSl8gTvHjFiAqLlL2Sui', 'student', 'active', '2025-08-26 14:46:25', NULL, NULL, NULL),
(4, 'STU68cfd0ae62c3c', 'Ved Samadhan Gunjal', 'ved1@gmail.com', '$2y$10$6ZfAi0IedFKIhYjMujGihuz5eOGxmP5CECqVnuvTiwRbvZLZfDNjW', 'student', 'active', '2025-09-21 10:17:18', '07028654979', NULL, NULL),
(5, 'ADM68cfdcffa1e10', 'Ved Samadhan Gunjal', 'vedg@gmail.com', '$2y$10$lcqyKWMsDNeb7D9p7RTNLOy0yrygjOJhQ3wlbvXA6CegdSwZ3SOOC', 'admin', 'active', '2025-09-21 11:09:51', '07028654979', NULL, NULL),
(6, 'ADM68d0202cd8545', 'Ved Samadhan Gunjal', 'test123@gmail.com', '$2y$10$6kJK7hOsdLqxFpV4t53oKuYPOkcVSW7ywEtgEngE8fxDch1i.KFBW', 'admin', 'active', '2025-09-21 15:56:28', '07028654979', NULL, NULL);

--
-- Indexes for dumped tables
--

--
-- Indexes for table `proctor_alerts`
--
ALTER TABLE `proctor_alerts`
  ADD PRIMARY KEY (`id`),
  ADD KEY `idx_alert_attempt` (`attempt_token`);

--
-- Indexes for table `proctor_recordings`
--
ALTER TABLE `proctor_recordings`
  ADD PRIMARY KEY (`id`);

--
-- Indexes for table `questions`
--
ALTER TABLE `questions`
  ADD PRIMARY KEY (`id`),
  ADD KEY `test_id` (`test_id`);

--
-- Indexes for table `tests`
--
ALTER TABLE `tests`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `test_id` (`test_id`),
  ADD KEY `created_by` (`created_by`);

--
-- Indexes for table `test_eligibility`
--
ALTER TABLE `test_eligibility`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `unique_test_student` (`test_id`,`student_id`),
  ADD KEY `student_id` (`student_id`),
  ADD KEY `assigned_by` (`assigned_by`);

--
-- Indexes for table `test_results`
--
ALTER TABLE `test_results`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `unique_attempt` (`test_id`,`student_id`,`attempt_token`),
  ADD KEY `student_id` (`student_id`);

--
-- Indexes for table `test_submissions`
--
ALTER TABLE `test_submissions`
  ADD PRIMARY KEY (`id`),
  ADD KEY `student_id` (`student_id`),
  ADD KEY `question_id` (`question_id`),
  ADD KEY `test_id` (`test_id`,`student_id`,`attempt_token`);

--
-- Indexes for table `users`
--
ALTER TABLE `users`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `user_id` (`user_id`),
  ADD UNIQUE KEY `email` (`email`);

--
-- AUTO_INCREMENT for dumped tables
--

--
-- AUTO_INCREMENT for table `proctor_alerts`
--
ALTER TABLE `proctor_alerts`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `proctor_recordings`
--
ALTER TABLE `proctor_recordings`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `questions`
--
ALTER TABLE `questions`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=5;

--
-- AUTO_INCREMENT for table `tests`
--
ALTER TABLE `tests`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=3;

--
-- AUTO_INCREMENT for table `test_eligibility`
--
ALTER TABLE `test_eligibility`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=5;

--
-- AUTO_INCREMENT for table `test_results`
--
ALTER TABLE `test_results`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=14;

--
-- AUTO_INCREMENT for table `test_submissions`
--
ALTER TABLE `test_submissions`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=18;

--
-- AUTO_INCREMENT for table `users`
--
ALTER TABLE `users`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=7;

--
-- Constraints for dumped tables
--

--
-- Constraints for table `questions`
--
ALTER TABLE `questions`
  ADD CONSTRAINT `questions_ibfk_1` FOREIGN KEY (`test_id`) REFERENCES `tests` (`id`) ON DELETE CASCADE;

--
-- Constraints for table `tests`
--
ALTER TABLE `tests`
  ADD CONSTRAINT `tests_ibfk_1` FOREIGN KEY (`created_by`) REFERENCES `users` (`id`);

--
-- Constraints for table `test_eligibility`
--
ALTER TABLE `test_eligibility`
  ADD CONSTRAINT `test_eligibility_ibfk_1` FOREIGN KEY (`test_id`) REFERENCES `tests` (`id`) ON DELETE CASCADE,
  ADD CONSTRAINT `test_eligibility_ibfk_2` FOREIGN KEY (`student_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,
  ADD CONSTRAINT `test_eligibility_ibfk_3` FOREIGN KEY (`assigned_by`) REFERENCES `users` (`id`);

--
-- Constraints for table `test_results`
--
ALTER TABLE `test_results`
  ADD CONSTRAINT `test_results_ibfk_1` FOREIGN KEY (`test_id`) REFERENCES `tests` (`id`) ON DELETE CASCADE,
  ADD CONSTRAINT `test_results_ibfk_2` FOREIGN KEY (`student_id`) REFERENCES `users` (`id`) ON DELETE CASCADE;

--
-- Constraints for table `test_submissions`
--
ALTER TABLE `test_submissions`
  ADD CONSTRAINT `test_submissions_ibfk_1` FOREIGN KEY (`test_id`) REFERENCES `tests` (`id`) ON DELETE CASCADE,
  ADD CONSTRAINT `test_submissions_ibfk_2` FOREIGN KEY (`student_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,
  ADD CONSTRAINT `test_submissions_ibfk_3` FOREIGN KEY (`question_id`) REFERENCES `questions` (`id`) ON DELETE CASCADE;
COMMIT;

/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
