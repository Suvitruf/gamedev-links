# Gamedev links

This site is a collection of links to various resources for game developers.

Basic mockup: docs/Aseets/mockup.png

![](.docs/Aseets/mockup.png)

Basic implementation consists of 2 main parts:
- The page with filterable table
- Admin page to edit information

## Description

In /raw directory there is a json file with all needed data to show. Format:
```
{
    "Link": string,
    "Title": string,
    "Author": string,
    "Type": string,
    "Language": string,
    "Tags": string[]
}
```

- Link. Link to the site/material.
- Title. Resource/material title.
- Author. Author's name.
- Type. String. One from the list: article, video, site, unknown.
- Language. ISO country code.
- Tags. List of custom tags. E.g. tutorial, showcase, demo, etc.


## Logic

On the opening it should load json file, parse it and fill table with data.

Additionaly by pressing Edit button it should switch to edit mode, where I can add, update, delete items in the table. Json file in memmory should be updated too.

When user press Save (which appeared in edit-mode), it should ask me to save this json file, so user will be able to download it.