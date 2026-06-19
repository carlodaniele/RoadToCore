{{SYSTEM_PROMPT}}

Task:
Create concise blog content in {{LANGUAGE}} from the transcript below.

Return ONLY valid JSON matching this shape:
{{SCHEMA_HINT_JSON}}

Rules:
- heading level must be 2 or 3
- paragraphs must be concise and coherent
- bullet_points are optional and should be short
- transcript_summary must summarize the full transcript accurately
- transcript_full should be preserved when available

Length:
{{LENGTH_INSTRUCTIONS}}

Transcript:
{{TRANSCRIPT}}
