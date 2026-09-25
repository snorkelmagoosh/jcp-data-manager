# jcp-data-manager

CLI toolkit for JCP data cleaning, job posting, and job expiration checks.


## jcp-data-manager example usage jupyter notebook:
[Example Jupyter Notebook](https://colab.research.google.com/drive/1Yn3HqDvnk4gc9Ype6TufdMNEng78nqlG?usp=sharing)


## What it does

- Takes JCP data and cleans (only slightly) the JSON export
- By default, for signed in users, runs name and facial analysis to get demographic data
- Auto-posts to WordPress
- Checks existing WordPress drafts for dead or soft-404 source links and can move invalid posts to private


## Usage
There are two main methods of usage:
1. pip install the package (choose this if you are unsure)
2. clone the repo (do this if you want to develop further the `jcp-data-manager`)

## Install
Using pip: `pip install jcp-data-manager`

For development: `git clone https://github.com/porterolson/jcp-data-manager.git`

## Configuration

To use the `jcp-data-manager` you need to configure some environment variables. 

The variables are:

```bash
WORDPRESS_BASE_URL
WORDPRESS_USERNAME
WORDPRESS_APP_PASSWORD
WORDPRESS_FEATURED_MEDIA_ID
GITHUB_MODELS_TOKEN
GITHUB_MODELS_ENDPOINT
GITHUB_MODELS_MODEL
GEMINI_API_KEY
GEMINI_MODEL
```

There are a couple of ways to set the environment variables, however the easiest is:
```bash
import os

os.environ["WORDPRESS_BASE_URL"] = "https://jobconnectionsproject.org/"
os.environ["WORDPRESS_USERNAME"] = "your-wp-username"
os.environ["WORDPRESS_APP_PASSWORD"] = "your-app-password"
os.environ["WORDPRESS_FEATURED_MEDIA_ID"] = "1807"

os.environ["GITHUB_MODELS_TOKEN"] = "your-github-models-token"
os.environ["GITHUB_MODELS_ENDPOINT"] = "https://models.github.ai/inference"
os.environ["GITHUB_MODELS_MODEL"] = "openai/gpt-4.1-mini"

os.environ["GEMINI_API_KEY"] = "your-gemini-api-key"
os.environ["GEMINI_MODEL"] = "gemini-2.5-flash-lite"
```

Below in the `Appendix` one can see how to get wp-username, wp-password, and tokens for the models along with caveats about model availability.

Additionally, the   environment variables can also be read from a `.env` file; however, this is primarily used for development. Once cloning the repo, one can consult `.env.example` to see how an `.env` file needs to be structured.


You can also point the CLI at a specific env file:

```bash
jcp-data-manager get-jobs --env-file /path/to/.env --occupation-title "Graphic Designer" --date-posted 04/21/2026 --location "Seattle, WA" --experiment 1
```

## Commands

### Cleaning Sessions Export JSON

The sessions file should be a top-level JSON object with a `sessions` key whose value is a list.

Example usage:
```bash
jcp-data-manager clean-json-data --sessions /content/jcpst-sessions-2026-04-21-17-27-55.json --output cleaned.parquet
```

where `--sessions` points to your `.json` file.


### Job scraping and posting

This command scrapes jobs, filters for qualification text, asks GitHub Models to format the posting HTML, saves the output dataset, and then posts WordPress drafts. **Note the posting script is not perfect and will need a human (RA)  to go thru the posts and check/clean them up before publishing**

Example Usage:

```bash
jcp-data-manager get-jobs --occupation-title "Graphic Designer" --date-posted 04/21/2026 --location "Seattle, WA" --experiment 1
```
Note that `--date-posted MM/DD/YYYY` is the earliest that you want the scraper/poster to look for jobs. For example, if today was `4/22/2026` and I supplied `--date-posted 4/7/2026`, then the automatic poster would look for jobs with the given title and location from April 7th to today (the 22nd).

_Further note that the day does not need to be zero padded (i.e. both `04/07/2026` and `4/7/2026` will work)_

By default, the job CLI command posts with the LinkedIn sign-in popup flow. To post without a linkedin sign in popup, use the flag `--no-linkedin` to switch to the non-LinkedIn post template.

Next, the `--experiment` flag indicates which treatment to use. Essentially flagging posts as experimental or not. `--experiment 0` will get job postings and post without any treatment; `--experiment 1` will post with the default treament randomization.

Lastly, use the `--skip-post` flag which skips the posting step. Use this flag if you only want the scraped and generated output file without creating WordPress drafts.

### Expiration checking

This command inspects WordPress posts, fetches each footnote URL, asks Gemini for a soft-404 probability, and by default changes invalid posts to `private`.

```bash
jcp-data-manager check-job-expiration --status publish --history-path .\expiration-state.json `--output .\expiration-state.json `
```

Use `--skip-private` if you want the report without updating WordPress post status.

The `--history-path` flag is used to specify where the location of the state file where post-check history is stored. Users can specify the `--output` path as the same path as the history, this will cause the history to be updated.

The `--max-posts-to-check` flag works as a limit for how many posts to check for expiring job ads. The default is 20.

### Seniority spread probing

This command scrapes and filters ads for one or more occupation x location cells the same way `get-jobs` does, classifies each ad into `entry`/`mid`/`senior` (title keywords first, years-of-experience stated in the ad body as a fallback), and reports the resulting tier spread per occupation. Use it before locking in occupation-specific seniority keyword lists or years-of-experience cutpoints in `seniority.py`, so those are set from an observed distribution of real ads rather than a guess.

```bash
jcp-data-manager check-seniority-spread --occupation-title "Paralegal" --occupation-title "Truck Driver" --location "Chicago, IL" --location "Boise, ID" --date-posted 9/1/2026
```

`--occupation-title` and `--location` can each be repeated; every occupation is probed against every location. Use `--output` to save the classified ad-level detail and `--summary-output` to save the aggregated occupation x tier table. Nothing is posted to WordPress by this command.


## Setting up Dev Repo
If you chose to clone the repo use `git`, then you will also need `uv` for the environment

### Installing UV
For PowerShell (Windows):
```bash
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

For Mac/Linux:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```
### Initalization

Then run `uv sync`

Add any subsequent packages with `uv add [PACKAGE NAME]` and then run `uv sync` again.

UV Docs can be accessed at `https://docs.astral.sh/uv/`

## (Appendix)

------------
#### (A.1) Getting Wordpress Username and Password
Start by emailing `Dr. Eastmond` and asking to be made an admin on Wordpress. This is the website building software we use to host and edit the JCP website, so you need access to be able to post job ads, remove job ads, edit website content, and access the data we collect.

If you don’t already have a Wordpress account, you’ll have to make one. It’s probably best to create it using your google account.

Once you have an account and are an admin, goto `https://jobconnectionsproject.org/wp-admin/index.php`

**ONCE AGAIN, DO NOT RUN THE UPDATER!!**

On the side menu goto `Users → Profile`

Scroll down to the bottom until you see this:
<img width="1698" height="571" alt="image" src="https://github.com/porterolson/jcp-data-manager/blob/main/imgs/wp-stuff.png?raw=True" />

Enter a new name for you application password (NOTE: this is not your username).

Click `Add Application Password`, make sure to save/write down the password Wordpress then generates for you, this is your `APP_PASSWORD`. Your `USERNAME` is simply your wordpress username (not the name of application password you just entered)

Put these in the scripts and you are ready to use Wordpress API!

----------

#### (A.2) Getting GitHub Models Token

_NOTE: GitHub may change access to older models (i.e. gpt 4.1-mini), if this model is no longer available, the following instrucitons will still help you generate a GitHub models token. Further, you can change `GITHUB_MODELS_MODEL` to some other model._

First, sttart by creating a GitHub account.

Next goto `https://github.com/marketplace/models/azure-openai/gpt-4-1-mini`

Click on "Use This Model"
<img width="1917" height="937" alt="image" src="https://github.com/porterolson/jcp-data-manager/blob/main/imgs/use-this-model.png?raw=True" />


Click "Create Personal Access Token"
<img width="765" height="284" alt="image" src="https://github.com/porterolson/jcp-data-manager/blob/main/imgs/personal-access-token.png?raw=True" />

Leave all the settings at their default and pick an expiration date (your token will no longer work after this date), and then click `Generate Token` at the bottom of the page. This is your `GITHUB_MODELS_TOKEN`

You are now ready to use GitHub Models!

--------
#### (A.3) Getting Gemeni Token

_NOTE: As of making this Google's models have much higher input token limit, so I use Gemeni models to look at html of pages to determine soft 404 errors. I do not use GitHub because Azure limits input tokens to ~8000; if someone is feeling ambitious, one future change may be to rewrite the code so that we only need to use one model and one API KEY_

_Also, Google may change access to their older models as well; if so change `GEMINI_MODEL` to a newer model_


First, goto `https://ai.google.dev/gemini-api/docs`

Click "Get API Key"

<img width="1915" height="836" alt="image" src="https://github.com/porterolson/jcp-data-manager/blob/main/imgs/api-key-gemeni.png?raw=True">


Sign in to your google account.

Click on this:
<img width="1918" height="515" alt="image" src="https://github.com/porterolson/jcp-data-manager/blob/main/imgs/gemeni-api-key-locatin.png?raw=True" />


You are now ready to use Gemeni in your code!


