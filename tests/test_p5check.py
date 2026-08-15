"""Subprocess tests for the protected p5 smoke checker."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SEED = ROOT / "src" / "physics_toy_factory" / "workspace_seed"
CHECKER = SEED / "p5check.js"
BROKEN = ROOT / "src" / "physics_toy_factory" / "demo_fixtures" / "broken_sketch.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="Node is required to execute p5check.js")


def sketch(body: str = "background(0);", *, setup_body: str = "createCanvas(320, 240);") -> str:
    return f"function setup() {{ {setup_body} }}\nfunction draw() {{ {body} }}\n"


def run_checker(tmp_path: Path, source: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    shutil.copy2(CHECKER, tmp_path / "p5check.js")
    (tmp_path / "sketch.js").write_text(source, encoding="utf-8")
    command = [NODE or "node", "p5check.js", *(arguments or ("sketch.js",))]
    return subprocess.run(command, cwd=tmp_path, check=False, capture_output=True, text=True, timeout=5)


def assert_pass(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("P5CHECK PASS frames=5 draw_calls=")
    assert result.stderr == ""


def assert_fail(result: subprocess.CompletedProcess[str], evidence: str, *, code: int = 1) -> None:
    assert result.returncode == code
    assert result.stdout == ""
    assert result.stderr.startswith("P5CHECK FAIL ")
    assert evidence in result.stderr


def test_minimal_valid_sketch_passes(tmp_path: Path) -> None:
    assert_pass(run_checker(tmp_path, sketch()))


@pytest.mark.parametrize(
    ("source", "evidence"),
    [
        ("function draw() { background(0); }", "setup must be a callable function"),
        ("function setup() { createCanvas(10, 10); }", "draw must be a callable function"),
        (sketch(setup_body="frameRate(30);"), "setup must call createCanvas"),
        (sketch("fill(255); stroke(0);"), "visible drawing primitive"),
    ],
)
def test_required_lifecycle_canvas_and_drawing_are_enforced(
    tmp_path: Path, source: str, evidence: str
) -> None:
    assert_fail(run_checker(tmp_path, source), evidence)


def test_syntax_error_has_bounded_useful_evidence(tmp_path: Path) -> None:
    assert_fail(run_checker(tmp_path, "function setup( {"), "SyntaxError")


def test_lifecycle_runtime_error_has_useful_evidence(tmp_path: Path) -> None:
    assert_fail(run_checker(tmp_path, sketch('throw new TypeError("draw exploded");')), "draw exploded")


def test_known_broken_repair_fixture_fails(tmp_path: Path) -> None:
    result = run_checker(tmp_path, BROKEN.read_text(encoding="utf-8"))
    assert_fail(result, "blendMdoe is not defined")


@pytest.mark.parametrize(
    "name",
    [
        "require",
        "module",
        "exports",
        "process",
        "Buffer",
        "fetch",
        "XMLHttpRequest",
        "WebSocket",
        "localStorage",
        "sessionStorage",
        "indexedDB",
        "document",
        "window",
        "location",
        "navigator",
        "Worker",
        "WebAssembly",
        "globalThis",
    ],
)
def test_forbidden_node_network_storage_and_dom_identifiers_fail(tmp_path: Path, name: str) -> None:
    source = sketch(f"background(0); typeof {name};")
    assert_fail(run_checker(tmp_path, source), f"Forbidden identifier: {name}")


@pytest.mark.parametrize(
    ("expression", "evidence"),
    [
        ("import('x')", "dynamic import"),
        ("eval('1 + 1')", "eval"),
        ("Function('return 1')()", "dynamic Function"),
        ("({}).constructor", "constructor access"),
        ("Object.getPrototypeOf({})", "prototype mutation"),
        ("new Proxy({}, {})", "metaprogramming"),
    ],
)
def test_dynamic_code_and_escape_constructs_fail(
    tmp_path: Path, expression: str, evidence: str
) -> None:
    assert_fail(run_checker(tmp_path, sketch(f"background(0); {expression};")), evidence)


@pytest.mark.parametrize(
    "name",
    [
        "loadImage",
        "loadJSON",
        "loadSound",
        "createVideo",
        "createElement",
        "createGraphics",
        "WEBGL",
        "shader",
    ],
)
def test_known_unsupported_p5_apis_have_specific_failure(tmp_path: Path, name: str) -> None:
    assert_fail(run_checker(tmp_path, sketch(f"background(0); {name};")), f"Unsupported p5 API: {name}")


def test_misspelled_p5_call_fails_instead_of_becoming_noop(tmp_path: Path) -> None:
    assert_fail(run_checker(tmp_path, sketch("background(0); blendMdoe(BLEND);")), "blendMdoe is not defined")


def test_infinite_loop_times_out(tmp_path: Path) -> None:
    result = run_checker(tmp_path, sketch("while (true) {}"))
    assert_fail(result, "timed out")


def test_captured_console_and_error_output_are_bounded(tmp_path: Path) -> None:
    passing = run_checker(tmp_path, sketch('console.log("x".repeat(1000000)); background(0);'))
    assert_pass(passing)
    failing = run_checker(tmp_path, sketch('throw new Error("x".repeat(10000));'))
    assert failing.returncode == 1
    assert len(failing.stderr) < 650


def test_checker_never_modifies_sketch_or_trusted_assets(tmp_path: Path) -> None:
    source = sketch("background(0); circle(20, 20, 10);")
    trusted = [CHECKER, SEED / "P5_API.md", SEED / "shell" / "index.html", SEED / "shell" / "p5.min.js"]
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in trusted}
    result = run_checker(tmp_path, source)
    sketch_before = hashlib.sha256((tmp_path / "sketch.js").read_bytes()).hexdigest()

    assert_pass(result)
    assert hashlib.sha256((tmp_path / "sketch.js").read_bytes()).hexdigest() == sketch_before
    assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in trusted} == before


def test_input_contract_requires_exact_relative_sketch_path(tmp_path: Path) -> None:
    shutil.copy2(CHECKER, tmp_path / "p5check.js")
    (tmp_path / "sketch.js").write_text(sketch(), encoding="utf-8")
    for arguments in ((), ("./sketch.js",), ("sketch.js", "extra")):
        result = subprocess.run(
            [NODE or "node", "p5check.js", *arguments],
            cwd=tmp_path,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert_fail(result, "usage: node p5check.js sketch.js", code=2)


def test_missing_empty_non_file_and_oversized_inputs_fail(tmp_path: Path) -> None:
    cases = ["missing", "empty", "directory", "oversized"]
    for case in cases:
        case_dir = tmp_path / case
        case_dir.mkdir()
        shutil.copy2(CHECKER, case_dir / "p5check.js")
        if case == "empty":
            (case_dir / "sketch.js").write_bytes(b"")
        elif case == "directory":
            (case_dir / "sketch.js").mkdir()
        elif case == "oversized":
            (case_dir / "sketch.js").write_bytes(b"x" * 100_001)
        result = subprocess.run(
            [NODE or "node", "p5check.js", "sketch.js"],
            cwd=case_dir,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert_fail(result, "sketch.js")


@pytest.mark.parametrize(
    "callback",
    [
        "windowResized",
        "mousePressed",
        "mouseReleased",
        "mouseMoved",
        "mouseDragged",
        "keyPressed",
        "keyReleased",
    ],
)
def test_each_defined_callback_is_exercised(tmp_path: Path, callback: str) -> None:
    source = sketch() + f'function {callback}() {{ throw new Error("{callback} exercised"); }}\n'
    assert_fail(run_checker(tmp_path, source), f"{callback} exercised")


CATALOG_CASES = {
    "canvas_environment": sketch(
        "background(0); frameRate(30); millis(); width + height + windowWidth + windowHeight + "
        "frameCount + deltaTime;",
        setup_body="createCanvas(320, 240); resizeCanvas(321, 241);",
    ),
    "input": sketch(
        "background(0); mouseX + mouseY + pmouseX + pmouseY + keyCode; "
        "mouseIsPressed || keyIsPressed || key;"
    ),
    "drawing": sketch(
        "background(0); clear(); point(1,1); line(0,0,1,1); triangle(0,0,1,0,1,1); "
        "quad(0,0,1,0,1,1,0,1); rect(1,1,2,2); square(1,1,2); ellipse(1,1,2,3); "
        "circle(1,1,2); arc(1,1,2,2,0,PI); bezier(0,0,1,1,2,2,3,3); curve(0,0,1,1,2,2,3,3);"
    ),
    "shapes": sketch(
        "background(0); beginShape(); vertex(0,0); curveVertex(1,1); "
        "bezierVertex(1,1,2,2,3,3); endShape(CLOSE);"
    ),
    "style": sketch(
        "background(0); fill(255); noFill(); stroke(255); noStroke(); strokeWeight(2); "
        "strokeCap(ROUND); strokeJoin(BEVEL); colorMode(RGB); rectMode(CORNER); "
        "ellipseMode(CENTER); blendMode(BLEND); circle(1,1,1);"
    ),
    "text": sketch('background(0); textSize(16); textAlign(CENTER); text("toy", 5, 5);'),
    "transform": sketch(
        "background(0); push(); translate(1,2); rotate(PI); scale(2); angleMode(RADIANS); pop();"
    ),
    "color": sketch(
        "const a=color(1,2,3,4), b=color(5,6,7,8); red(a)+green(a)+blue(a)+alpha(a); "
        "fill(lerpColor(a,b,0.5)); background(0);"
    ),
    "random_noise": sketch(
        "randomSeed(1); noiseSeed(2); noiseDetail(2, 0.5); random(); random(2); random(1,2); "
        "random([1,2]); noise(0.1,0.2); background(0);"
    ),
    "math": sketch(
        "abs(-1)+ceil(1.1)+constrain(2,0,1)+dist(0,0,1,1)+exp(1)+floor(1.9)+lerp(0,1,.5)+"
        "log(2)+mag(1,1)+map(1,0,2,0,10)+max(1,2)+min(1,2)+norm(1,0,2)+pow(2,2)+round(1.4)+"
        "sq(2)+sqrt(4)+sin(1)+cos(1)+tan(1)+asin(.5)+acos(.5)+atan(1)+atan2(1,1)+degrees(PI)+"
        "radians(180); background(0);"
    ),
    "constants": sketch(
        "PI+TWO_PI+HALF_PI+QUARTER_PI+TAU; [CENTER,CORNER,CORNERS,RADIUS,CLOSE,OPEN,CHORD,PIE,RGB,"
        "HSB,HSL,DEGREES,RADIANS,ADD,BLEND,ROUND,SQUARE,PROJECT,MITER,BEVEL]; background(0);"
    ),
    "vectors": sketch(
        "const a=createVector(1,2), b=a.copy().set(2,3).add(1,1).sub(1,1).mult(2).div(2); "
        "b.mag()+b.magSq()+b.normalize().setMag(2).limit(1).heading(); b.rotate(1).dot(a); b.dist(a); "
        "b.lerp(a,.5); p5.Vector.add(a,b); p5.Vector.sub(a,b); p5.Vector.mult(a,2); p5.Vector.div(a,2); "
        "p5.Vector.dist(a,b); p5.Vector.lerp(a,b,.5); p5.Vector.fromAngle(PI); p5.Vector.random2D(); "
        "background(0);"
    ),
}


@pytest.mark.parametrize(("family", "source"), CATALOG_CASES.items())
def test_every_documented_catalog_family_has_passing_fixture(
    tmp_path: Path, family: str, source: str
) -> None:
    assert family
    assert_pass(run_checker(tmp_path, source))


SUGGESTED_PROMPT_CASES = {
    "rain": sketch(
        "background(10,20,30); const x=random(width); const away=constrain(mouseX-x,-20,20); "
        "line(x-away,0,x,height);"
    ),
    "magnets": sketch(
        "background(0); const position=createVector(width/2,height/2); "
        "position.add(p5.Vector.random2D().limit(2)); circle(position.x,position.y,20);"
    ),
    "solar_system": sketch(
        "background(0); push(); translate(width/2,height/2); rotate(frameCount*.01); "
        "circle(0,0,30); circle(80,0,12); pop();"
    ),
    "fish": sketch(
        "background(0); const fish=createVector(width/2,height/2); "
        "fish.lerp(createVector(mouseX,mouseY),.1); ellipse(fish.x,fish.y,30,12);"
    ),
}


@pytest.mark.parametrize(("prompt", "source"), SUGGESTED_PROMPT_CASES.items())
def test_each_suggested_prompt_api_subset_passes(tmp_path: Path, prompt: str, source: str) -> None:
    assert prompt
    assert_pass(run_checker(tmp_path, source))


def test_documented_catalog_names_are_present() -> None:
    documentation = (SEED / "P5_API.md").read_text(encoding="utf-8")
    required = {
        "setup",
        "draw",
        "createCanvas",
        "background",
        "beginShape",
        "fill",
        "text",
        "translate",
        "color",
        "random",
        "constrain",
        "PI",
        "createVector",
        "p5.Vector.random2D",
    }
    assert all(f"`{name}`" in documentation for name in required)
