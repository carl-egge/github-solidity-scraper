# Github Solidity Scraper

> A tool to collect and store Solidity files, their version history and some metadata from public GitHub repositories.

## Key features

* This script uses the [GitHub REST API](https://docs.github.com/en/rest) to collect data about Solidity repositories, files and commits.
* When the script is run, it creates a local database with information about Solidity files, their repositories, and their commit history.
* It uses the [GitHub Search API repositories endpoint](https://api.github.com/search/repositories)
* In order to expand the results it uses a technique called *stratified search*
* Request throttling is used to make optimal use of the limited API
* The search results can be filtered according to various criteria
 - If specified the script will only include data that falls under open source licenses in order to avoid copyright issues
 - You can also decide whether or not to include forks in the search
* The script is built using [Python](https://docs.python.org/3/) and the [requests](https://pypi.org/project/requests/) package

**Script Steps**
1. Stratified Search on GitHub Search API
2. For each repository collect files
3. For each file collect commit history
4. For each commit get content
5. Store in local sqlite database


## How To Use

**Getting Started:**
To clone and run this script, you will need [Python](https://www.python.org/downloads/) (version >= 3) and [Pip](https://pip.pypa.io/en/stable/) installed on your computer.
From your command line:

```bash
# Clone this repository
$ git clone https://github.com/carl-egge/github-solidity-scraper.git

# Go into the repository
$ cd github-solidity-scraper

# Install dependencies
$ python3 -m pip install -r requirements.txt

# Run the app (optionally use arguments)
$ python3 github-solidity-scraper.py [--github-token TOKEN]
```
<br>

**Usage:**
To customize the script manually you can use arguments and control the behavior. It is strongly recommended to state a GitHub access token using the `github-token` argument.
<br>

- `--database` : Specify the name of the database file that the results will be stored in (default: results.db)
- `--statistics` : Specify a name for a spreadsheet file that is used to store the sampling statistics. This file can be used to continue a previous search if the script get interrupted or throws an exception (default: sampling.csv)
- `--stratum-size` : This is the length of the size ranges into which the search population is partitioned (default: 5)
- `--min-size` : The minimum code size that is searched for (default: 1)
- `--max-size` : The maximum code size that is searched for (default: 393216)
- `--no-throttle` : Disable the request throttling
- `--license-filter` : When enabled the script filters the search only for repositories that fall under one of githubs [licenses](api.github.com/licenses)
- `--search-forks` : When enabled the search includes forks of repositories.
- `--github-token` : With this argument you should specify a personal access token for GitHub (by default, the environment variable GITHUB_TOKEN is used)

<br>

> **Note:**
> The GitHub API provides a limit of 60 requests per hour. If a personal access token is provided, this limit can be extended up to 5000 requests per hour. Therefore, **you should definitely specify an access token** or have it stored in the shell environment so that the script can run efficiently.
> More information on how to generate a personal access token can be found [here](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/creating-a-personal-access-token#creating-a-personal-access-token-classic).

## Showcase Smart Contract Repository

**The results.db:**
The output of the script will be a [SQLite](https://www.sqlite.org/index.html) database that consits of three tables: repo, file and comit. These tables store the information that the script collects.

* *repo:* This table holds data about the repositories that were found (e.g. `url`, `path`, `owner` ...)
* *file:* This table contains data about the Solidity files that were found (e.g. `path`, `sha` ...)
  - The `repo_id` is a foreign key and is associated to the repo that the file was found in.
* *comit:* The commits correspond to a file and are stored together with some metadata in this table. This table also holds the actual file content from a commit.  (e.g. `sha`, `message`, `content`, `file_id` ...)
  - The `file_id` is a foreign key and is associated to the file that the commit corresponds to.
  - Commit is a reserved keyword in SQLite therefore the tablename is `comit` with one `m`.

<br>

**Look At The Data:**
In order to view and analyse the data a SQLite interface is needed. If not yet installed you can use one of many free online graphical user interfaces like ...

  - https://sqliteonline.com/
  - https://sqliteviewer.app/
  
or you can download a free database interface such as ...
  
  - [DBeaver](https://dbeaver.io/)
  - [Adminer](https://www.adminer.org/).
  
Feel free to use any tool you want to look at the output data.

<br>

**The Showcase Database:**
To show the database scheme and some example data of Solidity files this repository contains a small [showcase.db](showcase.db) that can be investigated. This way you can look at some output without the need to run the script yourself.
The showcase database can again be viewed using your favorite SQLite interface.


## License

The MIT License (MIT). Please have a look at the [LICENSE.md](LICENSE.md) for more details.
