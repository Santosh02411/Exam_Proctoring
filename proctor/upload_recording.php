<?php
// proctor/upload_recording.php
// Accepts POST multipart chunk uploads and appends into a temp file.
// Security: ensure you check authentication/authorization in production.

session_start();
header('Content-Type: application/json');

// Basic checks
if ($_SERVER['REQUEST_METHOD'] !== 'POST') { http_response_code(405); echo json_encode(['error'=>'method']); exit; }

$attempt_token = $_POST['attempt_token'] ?? null;
$test_id = isset($_POST['test_id']) ? (int)$_POST['test_id'] : 0;
$chunkIndex = isset($_POST['chunkIndex']) ? (int)$_POST['chunkIndex'] : 0;
$isLast = isset($_POST['isLast']) ? (int)$_POST['isLast'] : 0;

if (!$attempt_token || !$test_id) { http_response_code(400); echo json_encode(['error'=>'missing']); exit; }
if (!isset($_FILES['chunk'])) { http_response_code(400); echo json_encode(['error'=>'no_chunk']); exit; }

// create uploads folder
$baseDir = __DIR__ . '/../uploads/proctor_recordings';
if (!is_dir($baseDir)) mkdir($baseDir, 0750, true);

// use attempt token to identify file (sanitize)
$safeToken = preg_replace('/[^a-zA-Z0-9_\-]/', '_', substr($attempt_token, 0, 100));
$tempPath = $baseDir . "/{$safeToken}.part";

// move uploaded chunk to temp and append
$uploadedTmp = $_FILES['chunk']['tmp_name'];
if (!is_uploaded_file($uploadedTmp)) { http_response_code(400); echo json_encode(['error'=>'bad_upload']); exit; }

// append chunk bytes to temp file
$bytes = file_get_contents($uploadedTmp);
if ($bytes === false) { http_response_code(500); echo json_encode(['error'=>'read_failed']); exit; }

if (file_put_contents($tempPath, $bytes, FILE_APPEND) === false) {
    http_response_code(500); echo json_encode(['error'=>'append_failed']); exit;
}

// Optionally remove tmp (PHP does that automatically after script ends)

// Respond with success and current temp size
$size = filesize($tempPath);
echo json_encode(['ok'=>1, 'chunkIndex'=>$chunkIndex, 'size'=>$size, 'isLast'=>$isLast]);
exit;
