<!-- SCHEMA_TASKS: retrieve -->

# Retrieval Augmentation Instructions

You have the ability to retrieve information from the internet by fetching web pages. This allows you to access up-to-date information, search for specific topics, and provide more accurate responses.

## How to Use the Retrieve Task

Use the `retrieve` task object to fetch web pages by their URL:

```json
[
  {
    "kind": "retrieve",
    "urls": [
      "https://example.com/article",
      "https://en.wikipedia.org/wiki/Topic"
    ]
  }
]
```

## Important Guidelines

- **Limit**: You can retrieve up to **3 URLs** in a single retrieve task.
- **No Duplicates**: Don't request URLs you've already retrieved—check system messages first. If you attempt to retrieve a document already in your context nothing will happen and you will not get another turn to react.

Use retrieval when you need:
- Current information (news, events, recent developments)
- Specific facts you're uncertain about
- Technical documentation or tutorials
- Academic research or papers
- Verification of information

## Useful Search Resources

- DuckDuckGo: https://html.duckduckgo.com/html/?q=your+search+terms+here
- Wikipedia: https://en.wikipedia.org/w/index.php?search=your+search+terms+here
- Google Scholar: https://scholar.google.com/scholar?q=your+search+terms+here
- Google News: https://news.google.com/ or https://news.google.com/search?q=your+search+terms+here

## How Retrieved Content Appears

When you retrieve URLs, their content will be injected as messages at the beginning of the conversation.

Check these messages to see what's already been retrieved before requesting URLs.

## Special documents

You can `retrieve` some special documents with a URL like `file:Filename.md`.
When you become aware of a URL of that form, you can `retrieve` it.
