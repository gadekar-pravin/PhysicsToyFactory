# Physics Toy Factory p5 API

This is the complete p5.js surface accepted by `p5check.js`. Generated `sketch.js` files must use
global mode, define `setup()` and `draw()`, call `createCanvas()`, and perform at least one visible draw
call. APIs not listed here are unsupported. Asset loading, networking, storage, DOM access, audio,
video, WebGL, shaders, offscreen graphics, and dynamic code generation are forbidden.

The checker is a bounded smoke judge, not a visual or semantic grader. It runs `setup()` once,
`draw()` for five deterministic frames, and each defined input callback once.

## Lifecycle callbacks

`setup`, `draw`, `windowResized`, `mousePressed`, `mouseReleased`, `mouseMoved`, `mouseDragged`,
`keyPressed`, `keyReleased`

## Canvas and environment

Functions: `createCanvas`, `resizeCanvas`, `frameRate`, `millis`

Values: `width`, `height`, `windowWidth`, `windowHeight`, `frameCount`, `deltaTime`

## Input

`mouseX`, `mouseY`, `pmouseX`, `pmouseY`, `mouseIsPressed`, `key`, `keyCode`, `keyIsPressed`

## 2D drawing

`background`, `clear`, `point`, `line`, `triangle`, `quad`, `rect`, `square`, `ellipse`, `circle`,
`arc`, `bezier`, `curve`

## Shapes

`beginShape`, `vertex`, `curveVertex`, `bezierVertex`, `endShape`

## Style

`fill`, `noFill`, `stroke`, `noStroke`, `strokeWeight`, `strokeCap`, `strokeJoin`, `colorMode`,
`rectMode`, `ellipseMode`, `blendMode`

## Text

`text`, `textSize`, `textAlign`

## Transform

`push`, `pop`, `translate`, `rotate`, `scale`, `angleMode`

## Color

`color`, `red`, `green`, `blue`, `alpha`, `lerpColor`

## Random and noise

`random`, `randomSeed`, `noise`, `noiseDetail`, `noiseSeed`

Randomness and input values are deterministic inside the checker. The browser uses p5.js normally.

## Math

`abs`, `ceil`, `constrain`, `dist`, `exp`, `floor`, `lerp`, `log`, `mag`, `map`, `max`, `min`,
`norm`, `pow`, `round`, `sq`, `sqrt`, `sin`, `cos`, `tan`, `asin`, `acos`, `atan`, `atan2`,
`degrees`, `radians`

## Constants

`PI`, `TWO_PI`, `HALF_PI`, `QUARTER_PI`, `TAU`, `CENTER`, `CORNER`, `CORNERS`, `RADIUS`, `CLOSE`,
`OPEN`, `CHORD`, `PIE`, `RGB`, `HSB`, `HSL`, `DEGREES`, `RADIANS`, `ADD`, `BLEND`, `ROUND`,
`SQUARE`, `PROJECT`, `MITER`, `BEVEL`

## Vectors

Create a vector with `createVector` as `createVector(x, y)`. Instance methods are `set`, `copy`, `add`, `sub`, `mult`,
`div`, `mag`, `magSq`, `normalize`, `setMag`, `limit`, `heading`, `rotate`, `dot`, `dist`, and `lerp`.

Static methods are `p5.Vector.add`, `p5.Vector.sub`, `p5.Vector.mult`, `p5.Vector.div`,
`p5.Vector.dist`, `p5.Vector.lerp`, `p5.Vector.fromAngle`, and `p5.Vector.random2D`.

## Explicitly unsupported examples

The checker reports `Unsupported p5 API: <name>` for recognized APIs outside this product contract,
including `loadImage`, `loadJSON`, `loadSound`, `createVideo`, `createElement`, `createGraphics`,
`WEBGL`, `WEBGPU`, `shader`, `loadShader`, `createCapture`, and `createAudio`.

Misspellings and all other unknown identifiers remain undefined and fail normally. The checker never
turns unknown calls into no-ops.
