"""Template fragments for WordPress job posts."""

from __future__ import annotations


BASE_JOB_DESCRIPTION_SYSTEM_PROMPT = """
You are a professional technical editor and formatter.

Your task is to take a raw job description and return a clean, well-structured HTML version of it.

Rules:
- Output valid HTML only (no explanations or markdown)
- Preserve the original meaning, structure, and content
- Fix spelling, grammar, punctuation, and obvious formatting errors
- Do NOT add new requirements, responsibilities, or benefits
- Do NOT remove any information
- Replace any company name with exactly: "the hiring company" (capitalize when needed)
- Do NOT invent missing details
- If the text contains lists, convert them to proper <ul><li> lists
- If the text contains headings, use appropriate <h2> or <h3> tags
- Use <p> tags for normal text
- Make the title only include the job title, no need to include "the hiring company"
- Include a header before the job description (<h2>) with the job title, in the header use the format 'job_title' - 'location', where location is the location provided (do not include 'US', just the state)
- Assume all of this will be placed within a body tag, thus no need to include !DOCTYPE, html, or body tags

If something is unclear or malformed, make the minimal correction needed to improve readability without changing intent.

Return only the final HTML.
""".strip()


EXPERIMENT_1_JOB_DESCRIPTION_PROMPT_SUFFIX = """

Lastly, in the HTML you return, under the list of job qualifications (which will almost always be a list: <ul></ul>) that are being posted incldue the following HTML:

<!-- Treatment text -->
<p id="treat0"></p>

<p id="treat1"><br>Don't meet every single requirement? If you're excited about this role but your past experience doesn't align perfectly with every qualification in the job description, we encourage you to apply anyways. You may be just the right candidate for this role.<br></p>

<p id="treat2"><br>Don't meet every single requirement? Most companies routinely hire individuals who lack some of the stated required skills. If you're excited about this role but your past experience doesn't align perfectly with every qualification in the job description, we encourage you to apply anyways. You may be just the right candidate for this role.<br></p>

<p id="treat3"><br>Don't meet every single requirement? Most companies routinely hire individuals who lack some of the stated required skills. Studies have shown that women are less likely to apply to jobs unless they meet every single qualification. If you're excited about this role but your past experience doesn't align perfectly with every qualification in the job description, we encourage you to apply anyways. You may be just the right candidate for this role.<br></p>

<div id="treat4"><p style="font-size: 115%; margin-top: 0;"><b>Tip from the Job Connections Project:</b></p><p><b><i>Don't meet every single requirement?</i></b> Most companies routinely hire individuals who lack some of the stated required skills. Studies have shown that women are less likely to apply to jobs unless they meet every single qualification. If you're excited about this role but your past experience doesn't align perfectly with every qualification in the job description, we encourage you to apply anyways. You may be just the right candidate for this role.<br></p></div>

<p>&nbsp;</p>
</div>

Also note the following:
    - sometimes companies label qualifications 'What you'll bring/need'
    - The qualifications may be followed by EEO or some other text (<p>), however make sure to put the treatment text directly under the list part (<ul>)

Return only the final HTML.
""".strip()

GENERAL_POST_STYLES = """
<style>
body {
    font-family: Arial, sans-serif;
    font-size: 14px;
}
.button-link {
  display: flex;
  justify-content: center;
  align-items: center;
  text-align: center;
  text-decoration: none;
  font-size: 18px;
}
.close-btn {
    display: block;
    font-size: 24px;
    font-weight: bold;
    cursor: pointer;
    color: white;
    background-color: black;
    text-align: center;
    padding: 5px;
    width: 20px;
    height: 20px;
    line-height: 15px;
    border-radius: 5pt;
}
.close-btn-container {
    position: absolute;
    top: 5%;
    right: 5%;
    z-index: 1010;
}
.popup-container {
    display: none;
    position: fixed;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    background-color: rgba(0, 0, 0, 0.5);
    z-index: 1000;
}
.popup {
    position: absolute;
    top: 10%;
    left: 10%;
    width: 80%;
    height: 80%;
    background-color: white;
    border-radius: 5px;
    overflow: auto;
    padding: 20px;
    box-sizing: border-box;
}
.popup-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 10px;
}
.close-btn {
    font-size: 24px;
    font-weight: bold;
    cursor: pointer;
}
.popup-content {
    font-size: 16px;
    line-height: 1.5;
}
</style>
""".strip()


EXPERIMENT_1_POST_BLOCK = """
<!-- wp:html -->
<script>
function fetchJcpSessionId() {
  return jQuery.ajax({
    url: '/wp-admin/admin-ajax.php',
    type: 'GET',
    dataType: 'json',
    data: {
      action: 'jcpst_get_session_id'
    }
  }).then(function(resp) {
    if (resp && resp.success && resp.data && resp.data.session_id) {
      return resp.data.session_id;
    }

    console.warn('jcpst_get_session_id returned no session_id', resp);
    return '';
  }).catch(function(err) {
    console.warn('Failed to fetch session_id', err);
    return '';
  });
}
</script>

<script>
function randomizeTreat() {
  var surveyNumber = Math.floor(1000000000 + Math.random() * 9000000000);
  localStorage.removeItem("surveyNumber");
  localStorage.removeItem("fakething");
  localStorage.setItem("surveyNumber", surveyNumber);

  let randomNumber = Math.floor(Math.random() * 8);
  let randomizeGroup;

  if (randomNumber >= 0 && randomNumber <= 3) {
    randomizeGroup = 0;
  } else if (randomNumber === 4) {
    randomizeGroup = 1;
  } else if (randomNumber === 5) {
    randomizeGroup = 2;
  } else if (randomNumber === 6) {
    randomizeGroup = 3;
  } else {
    randomizeGroup = 4;
  }

  var adUrl = window.location.href;
  localStorage.removeItem("adUrl");
  localStorage.setItem("adUrl", adUrl);

  var jobUrl = window.location.href;
  localStorage.removeItem("randomizeGroup");

  fetchJcpSessionId().then(function(sessionId) {
    console.log("Using session_id:", sessionId);

    jQuery.ajax({
      url: '/wp-content/themes/blank-canvas-4/wordpress-form/update-data-adpage.php',
      type: 'POST',
      data: {
        survey_id: surveyNumber,
        treatment_group: randomizeGroup,
        post_url: adUrl,
        job_ad_url: jobUrl,
        session_id: sessionId
      },
      success: function(response) {
        localStorage.setItem("randomizeGroup", response);
        var el = document.getElementById('treat' + response);
        if (el) {
          el.style.display = 'block';
        }
      },
      error: function(xhr, status, error) {
        console.error("Failed to save survey row:", status, error);
      }
    });
  });
}

document.addEventListener("DOMContentLoaded", randomizeTreat);
</script>
<!-- /wp:html -->

<style>
  #treat0 {
    display: none;
  }
  #treat1 {
    display: none;
  }
  #treat2 {
    display: none;
  }
  #treat3 {
    display: none;
  }
  #treat4 {
    display: none;
    position: relative;
    background-color: #fff;
    border: 1px solid #ccc;
    padding: 20px;
    box-shadow: 0 0 10px rgba(0, 0, 0, 0.2);
    transition: right 0.3s ease;
    z-index: 9999;
    text-align: center;
    border-radius: 5px;
    font-size: 110%;
    margin-top: 1em;
    margin-bottom: 1em;
  }
</style>

""" + GENERAL_POST_STYLES


NON_EXPERIMENTAL_POST_BLOCK = GENERAL_POST_STYLES


COMMON_POST_FOOTER = """
The Job Connections Project is a non-profit company that advertises open positions for other companies. Please read the hiring company's job ad below, then click 'Continue'.
""".strip()


EXPERIMENT_POST_BLOCKS = {
    0: NON_EXPERIMENTAL_POST_BLOCK,
    1: EXPERIMENT_1_POST_BLOCK,
}




def build_google_html_block(google_script: str) -> str:
    return f"""
<!-- wp:html -->

{google_script}
<!-- /wp:html -->
""".strip()


def build_job_description_system_prompt(experiment: int) -> str:
    if experiment == 0:
        return BASE_JOB_DESCRIPTION_SYSTEM_PROMPT
    if experiment == 1:
        return f"{BASE_JOB_DESCRIPTION_SYSTEM_PROMPT}\n\n{EXPERIMENT_1_JOB_DESCRIPTION_PROMPT_SUFFIX}"

    raise ValueError(
        f"Unsupported experiment value: {experiment}. Supported values are 0 (non-experimental) and 1 (current treatment randomization)."
    )


def build_post_content(
    *,
    google_script: str,
    generated_html: str,
    experiment: int,
) -> str:
    try:
        experiment_block = EXPERIMENT_POST_BLOCKS[experiment]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported experiment value: {experiment}. Supported values are 0 (non-experimental) and 1 (current treatment randomization)."
        ) from exc

    base = f"{experiment_block}\n\n{COMMON_POST_FOOTER}"
    return f"{build_google_html_block(google_script)}\n\n{base}\n\n{generated_html.strip()}"
