DROP VIEW current_message_revisions;

ALTER TABLE source_revisions DROP COLUMN envelope_json;

CREATE VIEW current_message_revisions AS
SELECT messages.message_id, messages.chat_id, messages.position,
       revisions.revision_id, revisions.revision_number,
       revisions.destination_rel_path, revisions.speaker,
       revisions.message_timestamp, revisions.message_text,
       revisions.message_sha256, revisions.char_count
FROM messages
JOIN message_heads ON message_heads.message_id = messages.message_id
JOIN source_revisions revisions
  ON revisions.revision_id = message_heads.revision_id;

CREATE VIEW current_public_safety AS
SELECT heads.message_id, results.result_id,
       json_extract(results.output_json, '$.decision') AS decision,
       json_extract(results.output_json, '$.source.kind') AS source_kind,
       json_extract(results.output_json, '$.source.id') AS source_id
FROM stage_heads heads
JOIN stage_results results ON results.result_id = heads.result_id
WHERE heads.stage = 'public_safety';

CREATE VIEW current_public_safety_redactions AS
SELECT heads.message_id, results.result_id,
       CAST(redaction.key AS INTEGER) + 1 AS redaction_number,
       json_extract(redaction.value, '$.start') AS start,
       json_extract(redaction.value, '$.end') AS end,
       json_extract(redaction.value, '$.replacement') AS replacement,
       json_extract(redaction.value, '$.category') AS category
FROM stage_heads heads
JOIN stage_results results ON results.result_id = heads.result_id
JOIN json_each(results.output_json, '$.redactions') AS redaction
WHERE heads.stage = 'public_safety';

CREATE VIEW current_message_versions AS
SELECT heads.message_id, results.result_id,
       CASE heads.stage
           WHEN 'original_versions' THEN 'private'
           WHEN 'public_versions' THEN 'public'
       END AS audience,
       json_extract(version.value, '$.level') AS version,
       json_extract(version.value, '$.kind') AS storage_kind,
       json_extract(version.value, '$.message') AS message,
       json_extract(version.value, '$.source_level') AS pointer_source_level
FROM stage_heads heads
JOIN stage_results results ON results.result_id = heads.result_id
JOIN json_each(results.output_json, '$.versions') AS version
WHERE heads.stage IN ('original_versions', 'public_versions')
  AND json_extract(results.output_json, '$.kind') = 'versions';

CREATE VIEW current_discord_ranges AS
SELECT heads.message_id, results.result_id,
       CASE heads.stage
           WHEN 'discord_original' THEN 'private'
           WHEN 'discord_public' THEN 'public'
       END AS audience,
       json_extract(results.output_json, '$.source.kind') AS source_kind,
       json_extract(results.output_json, '$.source.id') AS source_id,
       json_extract(results.output_json, '$.source.sha256') AS source_sha256,
       json_extract(results.output_json, '$.max_chars') AS max_chars,
       json_extract(chunk.value, '$.index') AS chunk_index,
       json_extract(chunk.value, '$.start') AS start,
       json_extract(chunk.value, '$.end') AS end
FROM stage_heads heads
JOIN stage_results results ON results.result_id = heads.result_id
JOIN json_each(results.output_json, '$.chunks') AS chunk
WHERE heads.stage IN ('discord_original', 'discord_public')
  AND json_extract(results.output_json, '$.kind') = 'ranges';

CREATE VIEW current_message_mappings AS
SELECT heads.message_id, results.result_id,
       mapping.value AS title
FROM stage_heads heads
JOIN stage_results results ON results.result_id = heads.result_id
JOIN json_each(results.output_json, '$.discord_public_indexing') AS mapping
WHERE heads.stage = 'mapping';

CREATE VIEW current_product_pointers AS
SELECT heads.message_id, heads.stage, results.result_id,
       json_extract(results.output_json, '$.kind') AS pointer_kind,
       COALESCE(
           json_extract(results.output_json, '$.source.id'),
           json_extract(results.output_json, '$.source_result_id')
       ) AS target_id
FROM stage_heads heads
JOIN stage_results results ON results.result_id = heads.result_id
WHERE json_extract(results.output_json, '$.kind')
      IN ('source_pointer', 'result_pointer');
