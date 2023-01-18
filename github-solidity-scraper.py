#########################  GITHUB SOLIDITY SCRAPER  ############################

# This script exhaustively samples GitHub Repo Search results and stores
# Solidity files including their commit history and their content.
# Its main purpose is to build a local database of Solidity smart contracts and
# their versions. It is structured in a semi-chronological, readable form.

import os, sys, argparse, shutil, time, signal, re
import sqlite3, csv
import requests

# First we need to parse and validate arguments, check environment variables,
# set up the help text and so on.

# fix for argparse: ensure terminal width is determined correctly
os.environ['COLUMNS'] = str(shutil.get_terminal_size().columns)

parser = argparse.ArgumentParser(
    formatter_class=argparse.RawDescriptionHelpFormatter,
    description='''Exhaustively sample the GitHub Code Search API and 
store files and commits of Solidity smart contracts.''')

parser.add_argument('--database', metavar='FILE', default='results.db', 
    help='search results database file (default: results.db)')

parser.add_argument('--statistics', metavar='FILE', default='sampling.csv', 
    help='sampling statistics file (default: sampling.csv)')

parser.add_argument('--min-size', metavar='BYTES', type=int, default=1, 
    help='minimum code file size (default: 1)')

# Only files smaller than 384 KB are searchable via the GitHub API.
MAX_FILE_SIZE = 393216

parser.add_argument('--max-size', metavar='BYTES', type=int, 
    default=MAX_FILE_SIZE, 
    help=f'maximum code file size (default: {MAX_FILE_SIZE})')

parser.add_argument('--stratum-size', metavar='BYTES', type=int, default=5,
    help='''length of file size ranges into which population is partitioned 
    (default: 5)''')

parser.add_argument('--no-throttle', dest='throttle', action='store_false', 
    help='disable request throttling')

parser.add_argument('--search-forks', dest='forks', action='store_true', 
    help='''add 'fork:true' to query which includes forked repos in the result''')

parser.add_argument('--license-filter', dest='licensed', action='store_true', 
    help='filter the query with a list of open source licenses')

parser.add_argument('--github-token', metavar='TOKEN', 
    default=os.environ.get('GITHUB_TOKEN'), 
    help='''personal access token for GitHub 
    (by default, the environment variable GITHUB_TOKEN is used)''')

args = parser.parse_args()

if args.min_size < 1:
    sys.exit('min-size must be positive')
if args.min_size >= args.max_size:
    sys.exit('min-size must be less than or equal to max-size')
if args.max_size < 1:
    sys.exit('max-size must be positive')
if args.max_size > MAX_FILE_SIZE:
    sys.exit(f'max-size must be less than or equal to {MAX_FILE_SIZE}')
if args.stratum_size < 1:
    sys.exit('stratum-size must be positive')
if not args.github_token:
    confirm_no_token = input('''No GitHub TOKEN was specified or found in the environment variables.
Do you want to run the program without a token (this will slow the program down)? [y/N]\n''')
    if confirm_no_token.lower() == 'yes' or confirm_no_token.lower() == 'y':
        print("\nThe program will now run without a TOKEN (ratelimit at 60 requests per hour).\n")
        time.sleep(2)
    else:
        sys.exit("\nYou can specifiy a personal access token for GitHub using the '--github-token' argument.")
            

#-------------------------------------------------------------------------------

# The GitHub Code Search API is limited to 1000 results per query. To get around
# this limitation, we can take advantage of the ability to restrict searches to
# files of a certain size. By repeatedly searching with the same query but
# different size ranges, we can reach a pretty good sample of the overall
# population. This is a technique known as *stratified sampling*. The strata in
# our case are non-overlapping file size ranges.

# Let's start with some global definitions. We need to keep track of the first
# and last size in the current stratum...

strat_first = args.min_size
strat_last = min(args.min_size + args.stratum_size - 1, args.max_size)

# ...as well as the current stratum's population of repositories and the amount
# of repositories/files/commits that have been sampled so far (in the current 
# stratum). A value of -1 indicates "unknown".

pop_repo = -1
sam_repo = -1
sam_file = -1
sam_comit = -1

# We also keep track of the total (cumulative) sample sizes so far, and we store 
# it for the downloaded repos/files/commits respectivley.

total_sam_repo = -1
total_sam_file = -1
total_sam_comit = -1

# We also want to keep track of the execution time of the script, therefore we 
# store the starting time. Additionally we store the ratelimit-used information
# to keep track of how many api_calls we can still use. And just for information 
# we count the total amount of github api calls that have been made.

start = time.time()
rate_used = 0
api_calls = 0

# Here we store list of opensource liscense keys for GitHub API. Please note that
# this list includes viral licenses that require a user to include the same license
# in a project if a specific file from the result set should be modified and 
# redistributed.

licenses = ['apache-2.0', 'agpl-3.0', 'bsd-2-clause', 'bsd-3-clause', 'bsl-1.0',
            'cc0-1.0', 'epl-2.0', 'gpl-2.0', 'gpl-3.0', 'lgpl-2.1', 'mit',
            'mpl-2.0', 'unlicense']
current_license = ''
current_cumulative_pop = 0

#-------------------------------------------------------------------------------

# During the search we want to display a table of all the strata sampled so far,
# plus the stratum currently being sampled, some summary information, and a
# status message. These last three items will be continuously updated to signal
# the progress that's being made.

# First, let's just print the table header.

print('                 ┌────────────┬────────────┬────────────┬────────────┐')
print('                 │  pop repo  │  sam repo  │  sam file  │ sam commit │')
print('                 ├────────────┼────────────┼────────────┼────────────┤')

# Now we define some functions to print information about the current stratum.
# By default, this will simply add a new line to the output. However, to be able
# to show live progress, there is also an option to overwrite the current line.

def print_stratum(overwrite=False):
    if overwrite:
        sys.stdout.write('\033[F\r\033[J')
    if strat_first == strat_last:
        size = '%d' % strat_first
    else:
        size = '%d .. %d' % (strat_first, strat_last)
    pop_str = str(pop_repo) if pop_repo > -1 else ''
    sam_repo_str = str(sam_repo) if sam_repo > -1 else ''
    sam_file_str = str(sam_file) if sam_file > -1 else ''
    sam_comit_str = str(sam_comit) if sam_comit > -1 else ''
    per = '%6.2f%%' % (sam_repo/pop_repo*100) if pop_repo > 0 else ''
    print('%16s │ %10s │ %10s │ %10s │ %10s │ %6s' % (size, pop_str, sam_repo_str, 
        sam_file_str, sam_comit_str, per))

# Another function will print the footer of the table, including summary
# statistics and the status message. Here we provide a separate function to
# clear the footer again.

status_msg = ''

def print_footer():
    if args.min_size == args.max_size:
        size = '%d' % args.min_size
    else:
        size = '%d .. %d' % (args.min_size, args.max_size)
    ratelimit = 60 if not args.github_token else 5000
    tot_sam_repo_str = str(total_sam_repo) if total_sam_repo > -1 else ''
    tot_sam_file_str = str(total_sam_file) if total_sam_file > -1 else ''
    tot_sam_comit_str = str(total_sam_comit) if total_sam_comit > -1 else ''
    print('                 ├────────────┼────────────┼────────────┼────────────┤')
    print('                 │  pop repo  │  sam repo  │  sam file  │ sam commit │')
    print('                 └────────────┴────────────┴────────────┴────────────┘')
    print('%16s   %10s   %10s   %10s   %10s   %6s' % (size, '', tot_sam_repo_str,
        tot_sam_file_str, tot_sam_comit_str, ''))
    print()
    print('Current queried license: ', current_license) if args.licensed else print()
    print('Current GitHub ratelimit: %d / ~%d' % (rate_used, ratelimit))
    print()
    print(status_msg)

def clear_footer():
    sys.stdout.write(f'\033[9F\r\033[J')

# For convenience, we also have function for just updating the status message.
# It returns the old message so it can be restored later if desired.

def update_status(msg):
    global status_msg
    old_msg = status_msg
    status_msg = msg
    sys.stdout.write('\033[F\r\033[J')
    print(status_msg)
    return old_msg

#-------------------------------------------------------------------------------

# To access the GitHub API, we define a little helper function that makes an
# authorized GET request and throttles the number of requests per second so as
# not to run afoul of GitHub's rate limiting. Should a rate limiting error occur
# nonetheless, the function waits the appropriate amount of time before
# automatically retrying the request.

def get(url, params={}):
    global api_calls, rate_used
    if args.throttle:
        sleep = 60 if not args.github_token else 0.72
        time.sleep(sleep)
    auth_headers = {} if not args.github_token else {'Authorization': f'token {args.github_token}'}
    try:
        res = requests.get(url, params, headers=auth_headers)
    except requests.ConnectionError:
        print("\nERROR :: There seems to be a problem with your internet connection.")
        return signal_handler(0,0)
    api_calls += 1
    rate_used = (int(res.headers.get('X-RateLimit-Used')) if
        res.headers.get('X-RateLimit-Used') != None else 0)
    if res.status_code == 403:
        clear_footer()
        print_footer()
        return handle_rate_limit_error(res)
    else:
        if res.status_code != 200:
            handle_log_response(res)
        res.raise_for_status()
        return res

def handle_rate_limit_error(res):
    t = res.headers.get('X-RateLimit-Reset')
    if t is not None:
        t = max(0, int(int(t) - time.time()))
    else: 
        t = int(res.headers.get('Retry-After', 60))
    err_msg = f'Exceeded rate limit. Retrying after {t} seconds...'
    if not args.github_token:
        err_msg += ' Try running the script with a GitHub TOKEN.'
    old_msg = update_status(err_msg)
    time.sleep(t)
    update_status(old_msg)
    return get(res.url)

# In order to reduce the amount of GitHub API calls further we use the raw content API
# from GitHub to request the content of the single commits. This also reduces the need
# to throttle and hence makes the script theoretically faster. We define a function that
# helps to request data from the 'raw.githubusercontent.com/' API.

def get_content(url):
    try:
        res = requests.get(url)
    except requests.ConnectionError:
        print("\nERROR :: There seems to be a problem with your internet connection.")
        return signal_handler(0,0)
    if res.status_code != 200:
        handle_log_response(res)
    res.raise_for_status()
    return res

# This helper function can be used to write information on the Response from a request 
# into a log-file (default: log.txt).

def handle_log_response(res,file="log.txt"):
    err_msg = f'Request response error with status: {res.status_code} (for details see {file})'
    old_msg = update_status(err_msg)
    logger = open(file, "a")
    logging_str =  "\n\nTime: " + time.strftime("%m/%d/%Y, %H:%M:%S", time.localtime()) 
    logging_str += "\nRequest: " + str(res.url) + "\nStatus: "+ str(res.status_code)
    logging_str += "\nMessage: " + res.json()['message'] if res.status_code != 200 else ''
    logger.write(logging_str)
    logger.close()
    time.sleep(1.5)
    update_status(old_msg)

# We also define a convenient function to do the code search for a specific
# stratum. Note that we sort the search results by how recently a file has been
# updated (sort can be one of: stars, forks, help-wanted-issues, updated).
# We append search criteria 'fork' and 'license' depending on the user input 
# to refine the search results.

def search(a,b,order='asc',license="no"):
    q_fork = 'true' if args.forks else 'false'
    q_license = f'license:{license}' if license != "no" else ''
    query = f'language:Solidity size:{a}..{b} fork:{q_fork} {q_license}'
    
    return get('https://api.github.com/search/repositories',
        params={'q': query, 'sort': 'updated', 'order': order, 'per_page': 100})

#-------------------------------------------------------------------------------

# To download all repos/files/commits returned by a code search (up to the limit 
# of 1000 repo search results imposed by GitHub), we need to deal with pagination.
# On each page, we loop through all items and add them and their metadata to our
# results database (which will be set up in the next section), provided they're 
# not already in the database (which can happen when continuing a previous search).
# We filter the files in each repository and store only Solidity files. We then
# get the entire history of commits for each file, loop through all items using
# pagination again and store the commits in the results database.
# Also, if any of the repos or files or commits can not be downloaded, for whatever
# reason, they are simply skipped over and count as not sampled.

# DOWNLOAD REPOS
# For each repository we request a list of files from the master branch and filter 
# this list for Solidity files using the file extension (.sol).
# Note: The limit for the tree array is 100,000 entries with a maximum size of 7 MB 
# when getting the file list and using the recursive parameter.

def download_all_repos(res):
    download_repos_from_page(res)
    while 'next' in res.links:
        update_status('Getting next page of search results...')
        global pop_repo
        res = get(res.links['next']['url'])
        pop2 = res.json()['total_count'] + current_cumulative_pop
        pop_repo = max(pop_repo,pop2)
        download_repos_from_page(res)
        if sam_repo >= pop_repo:
            break
    update_status('')


def download_repos_from_page(res):
    update_status('Get list of files in repository...')
    for repo in res.json()['items']:
        if not known_repo(repo):
            insert_repo(repo)
            try:
                res = get("https://api.github.com/repos/" + repo["full_name"] 
                        + "/git/trees/" + repo["default_branch"] + "?recursive=1")
            except Exception:
                continue
            
            for file in res.json()['tree']:
                if(file['type'] == "blob" and bool(re.search(fr'\w\.sol$', file['path']))):
                    # Extract the file name from the path using regex
                    name_re = re.search(r'[\w-]+?(?=\.)', file['path'])
                    file['name'] = name_re.group(0) if name_re != None else file['path']
                    if not known_file(file, repo['id']):
                        file_id = insert_file(file, repo['id'])
                        download_all_commits(repo, file, file_id)

        clear_footer()
        print_stratum(overwrite=True)
        print_footer()
        if sam_repo >= pop_repo:
            return

# DOWNLOAD COMMITS
# For each of the files a list of commits is requested from the Github API 
# using the path as query on the commits endpoint.
# The list of commits will again be paginated (with 100 elements per page).
# Hence we loop over all pages and each of the commits on the pages. For a
# commit the file content is then downloaded from the Raw Github API that 
# has no rate limit.

def download_all_commits(repo, file, file_id):
    try:
        # Get the list of commits for this file
        commits_url = repo['commits_url'][:-6].replace('#', '%23')
        commits_res = get(commits_url, params={'path': file['path'], 'per_page': 100})
    except Exception:
        return
    download_commits_from_page(commits_res, repo['full_name'],
                                file['path'], file_id)
    while 'next' in commits_res.links:
        update_status('Getting next page of commits...')
        commits_res = get(commits_res.links['next']['url'])
        download_commits_from_page(commits_res, repo['full_name'],
                                    file['path'], file_id)
    update_status('')


def download_commits_from_page(commits_res, repo_full_name, file_path, file_id):
    count_commits = str(len(commits_res.json())) if len(commits_res.json()) < 100 else "100+"
    update_status('Downloading ' + count_commits + ' commits...')
    for commit in commits_res.json():
        if not known_commit(commit, file_id):
            try:
                content_res = get_content("https://raw.githubusercontent.com/" +
                    repo_full_name + "/" + commit['sha'] + "/" + file_path)
            except Exception:
                continue

            # Extract only shas of parents from api response
            parents = []
            for p in commit['parents']:
                parents.append(p['sha'])
            insert_commit(commit, content_res, parents, file_id)    

#-------------------------------------------------------------------------------

# This is a good place to open the connection to the results database, or create
# one if it doesn't exist yet. The database schema is similar to the GitHub API
# response schema. Our 'insert_repo', 'insert_file' and 'insert_comit' functions
# help to store the items in the database respectively. 'commit' is a reserved 
# keyword in sqlite, therefore the tablename is 'comit'. We also increase our 
# counter for the sample sizes after each insertion.

db = sqlite3.connect(args.database)
db.executescript('''
    CREATE TABLE IF NOT EXISTS repo 
    ( repo_id INTEGER PRIMARY KEY
    , name TEXT NOT NULL
    , full_name TEXT NOT NULL
    , description TEXT
    , url TEXT NOT NULL
    , fork INTEGER NOT NULL
    , owner_id INTEGER NOT NULL
    , owner_login TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS file
    ( file_id INTEGER PRIMARY KEY
    , name TEXT NOT NULL
    , path TEXT NOT NULL
    , sha TEXT NOT NULL
    , repo_id INTEGER NOT NULL
    , FOREIGN KEY (repo_id) REFERENCES repo(repo_id)
    , UNIQUE(path,repo_id)
    );
    CREATE TABLE IF NOT EXISTS comit
    ( comit_id INTEGER PRIMARY KEY
    , sha TEXT NOT NULL
    , message TEXT NOT NULL
    , size INTEGER NOT NULL
    , created DATETIME DEFAULT CURRENT_TIMESTAMP
    , content TEXT NOT NULL
    , compiler_version TEXT NOT NULL
    , parents TEXT NOT NULL
    , file_id INTEGER NOT NULL
    , FOREIGN KEY (file_id) REFERENCES file(file_id)
    , UNIQUE(sha,file_id)
    );
    ''')

def insert_repo(repo):
    db.execute('''
        INSERT OR IGNORE INTO repo 
            ( repo_id, name, full_name, description, url, fork
            , owner_id, owner_login
            )
        VALUES (?,?,?,?,?,?,?,?)
        ''',
        ( repo['id']
        , repo['name']
        , repo['full_name']
        , repo['description']
        , repo['url']
        , int(repo['fork'])
        , repo['owner']['id']
        , repo['owner']['login']
        ))
    db.commit()
    global sam_repo, total_sam_repo
    sam_repo += 1
    total_sam_repo += 1

# When inserting a file we check the file_id after insertion from the database
# cursor and return it for further computations.

def insert_file(file,repo_id):
    local_cur = db.execute('''
        INSERT OR IGNORE INTO file
            (name, path, sha, repo_id)
        VALUES (?,?,?,?)
        ''',
        ( file['name']
        , file['path']
        , file['sha']
        , repo_id
        ))
    file_id = local_cur.lastrowid
    db.commit()
    global sam_file, total_sam_file
    sam_file += 1
    total_sam_file += 1
    return file_id

# In order to get the byte size of the file content we check the length of the
# content of the response object. The timestamp is stored as the string directly
# from the API response, since sqlite can't store time objects anyway.
# The parent field stores a list of git_shas that correspond to the parent commits.

def insert_commit(commit,content_res,parents,file_id):
    db.execute('''
        INSERT OR IGNORE INTO comit
            (sha, message, size, created, content, compiler_version, parents, file_id)
        VALUES (?,?,?,?,?,?,?,?)
        ''',
        ( commit['sha']
        , commit['commit']['message']
        , len(content_res.content)
        , commit['commit']['committer']['date']
        , content_res.text
        , find_compiler_version(content_res.text)
        , str(parents)
        , file_id
        ))
    db.commit()
    global sam_comit, total_sam_comit
    sam_comit += 1
    total_sam_comit += 1

def known_repo(item):
    cur = db.execute("select count(*) from repo where full_name = ? and repo_id = ?",
        (item['full_name'], item['id']))
    return cur.fetchone()[0] == 1

def known_file(item, repo_id):
    cur = db.execute("select count(*) from file where path = ? and repo_id = ?",
        (item['path'], repo_id))
    return cur.fetchone()[0] == 1
    

def known_commit(item, file_id):
    cur = db.execute("select count(*) from comit where sha = ? and file_id = ?",
        (item['sha'], file_id))
    return cur.fetchone()[0] == 1

# For convenience, we define a short function that uses a regex to get the 
# compiler version of a Solidity file.

def find_compiler_version(text):
    compiler_vers = ""
    compiler_re = re.search(r'pragma solidity [<>^]?=?\s*([\d.]+)', text)
    if compiler_re != None:
        compiler_vers = compiler_re.group(1)
    return compiler_vers

#-------------------------------------------------------------------------------

# Now we can finally get into it! 

status_msg = 'Initialize Program'
print_footer()
total_sam_repo = 0
total_sam_file = 0
total_sam_comit = 0

# Before starting the iterative search process, let's see if we have a sampling
# statistics file that we could use to continue a previous search. If so, let's
# get our data structures and UI up-to-date; otherwise, create a new statistics
# file.

if os.path.isfile(args.statistics):
    update_status('Continuing previous search...')
    with open(args.statistics, 'r') as f:
        fr = csv.reader(f)
        next(fr) # skip header
        for row in fr:
            strat_first = int(row[0])
            strat_last = int(row[1])
            pop_repo = int(row[2])
            sam_repo = int(row[3])
            sam_file = int(row[4])
            sam_comit = int(row[5])
            total_sam_repo += sam_repo
            total_sam_file += sam_file
            total_sam_comit += sam_comit
            clear_footer()
            print_stratum()
            print_footer()
        if pop_repo > -1:
            strat_first += args.stratum_size
            strat_last = min(strat_last + args.stratum_size, args.max_size)
            pop_repo = -1
            sam_repo = -1
            sam_file = -1
            sam_comit = -1
else:
    with open(args.statistics, 'w') as f:
        f.write('stratum_first,stratum_last,population_repo,sample_repo,sample_file,sample_comit\n')

statsfile = open(args.statistics, 'a', newline='')
stats = csv.writer(statsfile)

#-------------------------------------------------------------------------------

# Let's also quickly define a signal handler to cleanly deal with Ctrl-C. If the
# user quits the program and cancels the search, we want to allow him to later
# continue more-or-less where he left of. So we need to properly close the
# database and statistic file.

def signal_handler(sig,frame):
    db.commit()
    db.close()
    statsfile.flush()
    statsfile.close()
    print("\nThe program took " + time.strftime("%H:%M:%S", 
        time.gmtime((time.time())-start)) + " to execute (Hours:Minutes:Seconds).")
    print("The program has requested " + str(api_calls) + " API calls from GitHub.")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

#-------------------------------------------------------------------------------

clear_footer()
print_stratum()
print_footer()

# Iterating through all the strata, we want to sample as much as we can.

while strat_first <= args.max_size:

    pop_repo = 0
    sam_repo = 0
    sam_file = 0
    sam_comit = 0

    # We check whether the search should filter for a license or not.

    if not args.licensed:

        update_status('Searching...')
        res = search(strat_first, strat_last)
        pop_repo = int(res.json()['total_count'])
        clear_footer()
        print_stratum(overwrite=True)
        print_footer()

        download_all_repos(res)

        # To stretch the 1000-results-per-query limit, we can simply repeat the
        # search with the sort order reversed, thus sampling the stratum population
        # from both ends, so to speak. This gives us a maximum sample size of 2000
        # per stratum.

        if pop_repo > 1000:
            update_status('Repeating search with reverse sort order...')
            res = search(strat_first, strat_last, order='desc')
            
            # Due to the instability of search results, we might get a different
            # population count on the second query. We will take the maximum of the
            # two population counts for this stratum as a conservative estimate.

            pop2 = int(res.json()['total_count'])
            pop_repo = max(pop_repo,pop2)
            clear_footer()
            print_stratum(overwrite=True)
            print_footer()

            download_all_repos(res)


    else:
        
        # Within the strata we loop through the list of licenses and search for
        # files with the 'license' filter.

        for lic in licenses:
            update_status(f'Searching for >>{lic}<< licensed repositories...')
            current_license = lic
            res = search(strat_first, strat_last,license=lic)
            current_cumulative_pop = pop_repo
            pop_repo += int(res.json()['total_count'])
            clear_footer()
            print_stratum(overwrite=True)
            print_footer()

            download_all_repos(res)

            if pop_repo > 1000:
                update_status('Repeating search with reverse sort order...')
                res = search(strat_first, strat_last, order='desc',license=lic)

                pop2 = int(res.json()['total_count']) + current_cumulative_pop
                pop_repo = max(pop_repo,pop2)
                clear_footer()
                print_stratum(overwrite=True)
                print_footer()

                download_all_repos(res)


    # After we've sampled as much as we could of the current strata, commit it
    # to the table and move on to the next one.

    stats.writerow([strat_first,strat_last,pop_repo,sam_repo,sam_file,sam_comit])
    statsfile.flush()
    
    strat_first += args.stratum_size
    strat_last = min(strat_last + args.stratum_size, args.max_size)
    pop_repo = -1
    sam_repo = -1
    sam_file = -1
    sam_comit = -1

    clear_footer()
    print_stratum()
    print_footer()

update_status('Done.')
print("The program took " + time.strftime("%H:%M:%S", time.gmtime((time.time())-start)) + 
    " to execute (Hours:Minutes:Seconds).")
print("The program has requested " + str(api_calls) + " API calls from GitHub.\n\n")
