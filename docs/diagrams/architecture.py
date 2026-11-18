"""Generates docs/images/architecture.png (the README's architecture picture).

Box titles for AWS, Google Cloud and FastMCP are logos from docs/diagrams/logos/ (see the README there).

Not needed to run the server; only to regenerate the picture after changing it:

    pip install diagrams          # also needs Graphviz: brew install graphviz
    python docs/diagrams/architecture.py
"""
from pathlib import Path

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.analytics import Athena, Glue
from diagrams.aws.compute import ECS, Lambda
from diagrams.aws.devtools import Codebuild
from diagrams.aws.management import Cloudwatch
from diagrams.aws.security import IdentityAndAccessManagementIamAWSSts as STS
from diagrams.aws.storage import S3
from diagrams.gcp.analytics import BigQuery
from diagrams.gcp.compute import Run
from diagrams.gcp.devtools import Build
from diagrams.gcp.operations import Logging
from diagrams.generic.network import Firewall
from diagrams.generic.storage import Storage
from diagrams.onprem.client import Client
from diagrams.programming.language import Python

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "images" / "architecture"
LOGOS = HERE / "logos"


def logo(name, width, height):
    """A box title that is an image instead of text (Graphviz HTML-like label)."""
    return (
        '<<TABLE BORDER="0" CELLBORDER="0" CELLPADDING="3"><TR>'
        f'<TD FIXEDSIZE="TRUE" WIDTH="{width}" HEIGHT="{height}"><IMG SRC="{LOGOS / name}" SCALE="TRUE"/></TD>'
        "</TR></TABLE>>"
    )

GRAPH = {
    "pad": "0.5",
    "nodesep": "0.55",
    "ranksep": "0.9",
    "fontname": "Helvetica",
    "fontsize": "16",
    "bgcolor": "white",
    "splines": "spline",
    "compound": "true",
}
NODE = {"fontname": "Helvetica", "fontsize": "13"}
LINK = {"color": "#6B7280", "penwidth": "1.6"}
INVISIBLE = Edge(style="invis")


def grid(columns):
    """Lay nodes out as a grid: each inner list is one column, left to right.
    Nodes in the same column sit one above the other; invisible edges between neighbouring
    columns keep them in order without drawing any line."""
    for left, right in zip(columns, columns[1:]):
        for a, b in zip(left, right):
            a >> INVISIBLE >> b


with Diagram(
    "",
    filename=str(OUT),
    outformat="png",
    show=False,
    direction="LR",
    graph_attr=GRAPH,
    node_attr=NODE,
    edge_attr=LINK,
):
    client = Client("MCP client\nClaude Code · Codex\nCursor · GitHub Copilot\netc.")
    config = Storage(".env  →  config.py\nvalidated environments")

    with Cluster("server.py  ·  FastMCP over stdio", graph_attr={"bgcolor": "#E6F4EA", "pencolor": "#2E7D32", "margin": "24", "labeljust": "l", "label": logo("fastmcp.png", 83, 60)}):
        server = Python("server.py\ncloud_tool wrapper\nworker thread · isError")
        safety = Firewall("Safety helpers\nSQL guard · byte cap\npagination · limits")

    with Cluster("Google Cloud", graph_attr={"bgcolor": "#E8F0FE", "pencolor": "#4285F4", "margin": "20", "labeljust": "l", "label": logo("google-cloud.png", 168, 80)}):
        bq, cb = BigQuery("BigQuery"), Build("Cloud Build")
        run, log = Run("Cloud Run"), Logging("Cloud Logging")
        # Graphviz puts the first node of each column at the bottom, so columns are listed bottom-to-top
        grid([[run, bq], [log, cb]])     # top row: BigQuery, Cloud Build

    with Cluster("AWS", graph_attr={"bgcolor": "#FFF4E0", "pencolor": "#FF9900", "margin": "20", "labeljust": "l", "label": logo("aws.png", 100, 60)}):
        ath, glue = Athena("Athena"), Glue("Glue Data Catalog")
        s3, cw = S3("S3"), Cloudwatch("CloudWatch Logs")
        ecs, lam = ECS("ECS / Fargate"), Lambda("Lambda")
        cdb, sts = Codebuild("CodeBuild"), STS("STS (whoami)")
        # equal-sized columns keep Graphviz from reshuffling the grid
        grid([[ecs, ath], [lam, glue], [cdb, s3], [sts, cw]])   # top row: Athena, Glue, S3, CloudWatch

    client >> Edge(label="  tool calls (stdio)  ", dir="both", fontname="Helvetica", fontsize="12") >> server
    config >> server
    server >> safety
    # one arrow per provider, drawn to the edge of its box: the server can call any service inside it
    safety >> Edge(color="#4285F4", lhead="cluster_Google Cloud", penwidth="2.4", tailport="e") >> bq
    safety >> Edge(color="#FF9900", lhead="cluster_AWS", penwidth="2.4", tailport="e") >> ath
