"""Generate test PDFs that look like the papers Refolio is meant for.

Run:  python tests/fixtures/make_fixtures.py
Needs pdflatex (two-column journal style) and pandoc + LibreOffice (Word style).
All text is original filler written for these tests.
"""
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).parent

PARAS = [
    "When we accept what another person tells us, we seem to acquire knowledge without "
    "ever inspecting the evidence ourselves. This familiar fact has struck many philosophers "
    "as puzzling, since it appears to make the epistemic standing of a belief depend on "
    "features of a speaker that the hearer cannot directly observe.",
    "One natural response holds that testimony is merely a species of inductive inference. "
    "On this view, the hearer reasons from the past reliability of speakers to the probable "
    "truth of the present report, and the justification transmitted is never more than the "
    "justification supplied by that background generalisation.",
    "The reductionist picture faces a well-known difficulty. Very few of us have checked "
    "enough reports to ground the sweeping generalisations the view requires, and children "
    "certainly have not, yet we do not ordinarily think that a child who learns the names of "
    "the planets from a teacher thereby fails to know them.",
    "Anti-reductionists respond by positing a default entitlement to accept what we are told "
    "in the absence of defeaters. The entitlement is not earned by the hearer's own inquiry; "
    "it is a standing feature of our cognitive situation as members of a linguistic community "
    "whose practices of assertion are governed by norms of sincerity and competence.",
    "It would be a mistake, however, to treat the dispute as a simple choice between two "
    "rival theses. Both parties agree that a hearer who ignores obvious signs of deception is "
    "epistemically criticisable, and both agree that the reliability of the speaker matters "
    "to whether knowledge is transmitted at all.",
    "What remains contested is the direction of explanation. The reductionist explains the "
    "default by appeal to evidence; the anti-reductionist explains the role of evidence by "
    "appeal to the default, treating counter-evidence as something that defeats an entitlement "
    "already in place rather than as something that must be accumulated before belief is "
    "permitted.",
]

NOTES = [
    "The locus classicus is usually taken to be the eighteenth-century debate, though the "
    "terms of that debate differ in important respects from the contemporary discussion.",
    "See the discussion of inductive support in the second section below, where the "
    "generalisation is stated more carefully.",
    "This objection is sometimes called the problem of the global justification of "
    "testimony; I set aside here the question of whether local justification fares better. "
    "It is worth stressing that the objection does not depend on any particular account of "
    "induction, and that it applies equally to probabilistic and non-probabilistic "
    "versions of the view. A full treatment would also need to consider the role of "
    "memory, since much of what we know from testimony we no longer remember having been "
    "told, and the reductionist must say how the original inductive support is preserved "
    "across that gap. I return to a version of this worry in the final section.",
    "Compare the parallel entitlement often claimed for perception and memory.",
    "I owe this way of putting the point to conversations with colleagues.",
]

QUOTE = (
    "To believe on the word of another is not to suspend judgement but to exercise it in a "
    "particular way, by placing one's reliance where one has reason to think it will be "
    "honoured."
)


def latex_two_column(out: Path):
    body = []
    note_i = 0

    def para(i, with_note=True):
        nonlocal note_i
        text = PARAS[i % len(PARAS)]
        if with_note and note_i < len(NOTES):
            # put the footnote after the first sentence
            first, rest = text.split(". ", 1)
            text = first + r".\footnote{" + NOTES[note_i] + "} " + rest
            note_i += 1
        return text

    body.append(r"\section{Introduction}")
    body += [para(0), para(1, False), para(2), para(3, False)]
    body.append(r"\begin{quote}" + QUOTE + r"\end{quote}")
    body.append(r"\section{The Reductionist Picture}")
    body += [para(4), para(5, False), para(0, False), para(1), para(2, False)]
    body.append(r"\subsection{A worry about children}")
    body += [para(3), para(4, False), para(5, False), para(0, False)]
    body.append(r"\section{Conclusion}")
    body += [para(1, False), para(2, False), para(3, False), para(4)]
    tex = r"""
\documentclass[10pt,twocolumn]{article}
\usepackage[margin=0.8in]{geometry}
\usepackage{fancyhdr}
\pagestyle{fancy}
\fancyhf{}
\fancyhead[L]{\small\itshape Journal of Imaginary Epistemology}
\fancyhead[R]{\small\thepage}
\fancyfoot[C]{\tiny This content downloaded from 128.59.10.4 on Tue, 01 Oct 2024 12:00:00 UTC. All use subject to the terms.}
\title{Trusting the Word of Others}
\author{A. N. Author}
\date{}
\begin{document}
\maketitle
\thispagestyle{fancy}
""" + "\n\n".join(body) + r"""
\end{document}
"""
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "paper.tex").write_text(tex)
        for _ in range(2):
            subprocess.run(["pdflatex", "-interaction=nonstopmode", "paper.tex"], cwd=d,
                           check=True, capture_output=True)
        (Path(d) / "paper.pdf").rename(out)


def word_single_column(out: Path):
    md = ["# On the Ethics of Belief", "", "*Second Author*", "", "## 1. The Question", ""]
    n = 0
    for k in range(9):
        text = PARAS[k % len(PARAS)]
        if k % 2 == 0 and n < len(NOTES):
            first, rest = text.split(". ", 1)
            text = f"{first}.[^{n+1}] {rest}"
            n += 1
        md += [text, ""]
        if k == 4:
            md += ["## 2. Evidence and Entitlement", ""]
    md += ["> " + QUOTE, ""]
    for i in range(n):
        md += [f"[^{i+1}]: {NOTES[i]}", ""]
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "paper.md"
        src.write_text("\n".join(md))
        subprocess.run(["pandoc", str(src), "-o", str(Path(d) / "paper.docx")], check=True)
        subprocess.run(["soffice", "--headless", "--convert-to", "pdf", "--outdir", d,
                        str(Path(d) / "paper.docx")], check=True, capture_output=True)
        (Path(d) / "paper.pdf").rename(out)


def latex_book_style(out: Path):
    """Single column, alternating running heads, a cover page, a note spanning pages."""
    body = []
    long_note = " ".join(NOTES[2:3] * 4)
    for k in range(14):
        text = PARAS[k % len(PARAS)]
        if k == 5:
            first, rest = text.split(". ", 1)
            text = first + r".\footnote{" + long_note + "} " + rest
        elif k % 3 == 0:
            first, rest = text.split(". ", 1)
            text = first + r".\footnote{" + NOTES[k % len(NOTES)] + "} " + rest
        if k == 4:
            body.append(r"\section*{Testimony and \emph{Trust}}")
        if k == 9:
            body.append(r"\begin{quote}" + QUOTE + r"\end{quote}")
        body.append(text.replace("tells us", r"\emph{tells} us"))
    tex = r"""
\documentclass[11pt,twoside]{article}
\usepackage[paperwidth=5.5in,paperheight=8.5in,margin=0.7in]{geometry}
\usepackage{fancyhdr}
\pagestyle{fancy}
\fancyhf{}
\fancyhead[LE]{\small\thepage\quad A. N. Author}
\fancyhead[RO]{\small Trusting the Word of Others\quad\thepage}
\begin{document}
\thispagestyle{empty}
\begin{center}\Large JSTOR\\[1em]\normalsize Trusting the Word of Others\\Stable URL: https://www.jstor.org/stable/0000000\\[2em]
Your use of the JSTOR archive indicates your acceptance of the Terms and Conditions of Use.\end{center}
\newpage
\setcounter{page}{117}
\begin{center}{\LARGE Trusting the Word of Others}\\[0.6em]{\large A. N. Author}\end{center}
\section*{Introduction}
""" + "\n\n".join(body) + r"""
\end{document}
"""
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "paper.tex").write_text(tex)
        for _ in range(2):
            subprocess.run(["pdflatex", "-interaction=nonstopmode", "paper.tex"], cwd=d,
                           check=True, capture_output=True)
        (Path(d) / "paper.pdf").rename(out)


if __name__ == "__main__":
    latex_book_style(HERE / "book_style_jstor.pdf")
    latex_two_column(HERE / "two_column_journal.pdf")
    word_single_column(HERE / "word_single_column.pdf")
    print("wrote", list(HERE.glob("*.pdf")))
