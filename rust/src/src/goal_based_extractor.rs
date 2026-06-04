// src/goal_based_extractor.rs  <- src/goal_based_extractor.py
//! Goal-based content extraction prompt inspired by Alibaba Tongyi DeepResearch.

/// `EXTRACTOR_PROMPT`
///
/// Note: this mirrors the Python triple-quoted string byte-for-byte. The
/// doubled braces (`{{` / `}}`) are part of the stored template — they become
/// literal single braces only when fed through Python's `str.format`, which is
/// not done here.
pub const EXTRACTOR_PROMPT: &str = r#"Please process the following webpage content and user goal to extract relevant information:

## **Webpage Content**
{webpage_content}

## **User Goal**
{goal}

## **Task Guidelines**
1. **Content Scanning for Rational**: Locate the **specific sections/data** directly related to the user's goal within the webpage content
2. **Key Extraction for Evidence**: Identify and extract the **most relevant information** from the content, you never miss any important information, output the **full original context** of the content as far as possible, it can be more than three paragraphs.
3. **Summary Output for Summary**: Organize into a concise paragraph with logical flow, prioritizing clarity and judge the contribution of the information to the goal.

**Final Output Format using JSON format has "rational", "evidence", "summary" fields**

Example output:
{{
    "rational": "This section discusses X which directly relates to the goal of understanding Y",
    "evidence": "Full quotes and context from the page...",
    "summary": "Concise summary of how this information answers the goal"
}}
"#;
