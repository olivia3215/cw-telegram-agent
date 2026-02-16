You are consolidating conversation summaries.

You will receive exactly five existing summary entries as JSON.

Produce one concise paragraph as plain text that preserves only the most important facts from all five summaries. Do not invent details. Do not reference message IDs, dates, or summary IDs in the output.

Return JSON matching the required schema with one field:
- `summary`: the consolidated paragraph text.
