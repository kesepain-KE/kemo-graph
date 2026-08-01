struct Params {
  nodeCount: u32,
  gridWidth: u32,
  gridHeight: u32,
  maxPerCell: u32,
  worldMin: vec2<f32>,
  worldSize: vec2<f32>,
  worldCenter: vec2<f32>,
  centerStrength: f32,
  repulsionStrength: f32,
  linkStrength: f32,
  linkDistance: f32,
  damping: f32,
  alpha: f32,
}

@group(0) @binding(0) var<storage, read> currentPositions: array<vec2<f32>>;
@group(0) @binding(1) var<storage, read> currentVelocities: array<vec2<f32>>;
@group(0) @binding(2) var<storage, read_write> nextPositions: array<vec2<f32>>;
@group(0) @binding(3) var<storage, read_write> nextVelocities: array<vec2<f32>>;
@group(0) @binding(4) var<storage, read> masses: array<f32>;
@group(0) @binding(5) var<storage, read_write> gridCounts: array<atomic<u32>>;
@group(0) @binding(6) var<storage, read_write> gridIndices: array<u32>;
@group(0) @binding(7) var<storage, read> adjacencyOffsets: array<u32>;
@group(0) @binding(8) var<storage, read> adjacencyTargets: array<u32>;
@group(0) @binding(9) var<storage, read> adjacencyWeights: array<f32>;
@group(0) @binding(10) var<storage, read> pinnedNodes: array<vec4<f32>>;
@group(0) @binding(11) var<storage, read> collisionRadii: array<f32>;
@group(0) @binding(12) var<uniform> params: Params;

fn gridCell(position: vec2<f32>) -> vec2<u32> {
  let cellWidth = params.worldSize.x / f32(params.gridWidth);
  let cellHeight = params.worldSize.y / f32(params.gridHeight);
  let normalized = position - params.worldMin;
  let cellX = u32(clamp(normalized.x / cellWidth, 0.0, f32(params.gridWidth - 1u)));
  let cellY = u32(clamp(normalized.y / cellHeight, 0.0, f32(params.gridHeight - 1u)));
  return vec2<u32>(cellX, cellY);
}

@compute @workgroup_size(128)
fn clearGrid(@builtin(global_invocation_id) invocation: vec3<u32>) {
  let cellCount = params.gridWidth * params.gridHeight;
  if (invocation.x < cellCount) {
    atomicStore(&gridCounts[invocation.x], 0u);
  }
}

@compute @workgroup_size(128)
fn binNodes(@builtin(global_invocation_id) invocation: vec3<u32>) {
  let index = invocation.x;
  if (index >= params.nodeCount) {
    return;
  }
  let cell = gridCell(currentPositions[index]);
  let cellIndex = cell.y * params.gridWidth + cell.x;
  let slot = atomicAdd(&gridCounts[cellIndex], 1u);
  if (slot < params.maxPerCell) {
    gridIndices[cellIndex * params.maxPerCell + slot] = index;
  }
}

@compute @workgroup_size(128)
fn integrate(@builtin(global_invocation_id) invocation: vec3<u32>) {
  let index = invocation.x;
  if (index >= params.nodeCount) {
    return;
  }
  let fixed = pinnedNodes[index];
  if (fixed.z > 0.5) {
    nextPositions[index] = fixed.xy;
    nextVelocities[index] = vec2<f32>(0.0, 0.0);
    return;
  }

  let position = currentPositions[index];
  var force = vec2<f32>(
    (params.worldCenter.x - position.x) * params.centerStrength * 0.0009 * params.alpha,
    (params.worldCenter.y - position.y) * params.centerStrength * 0.0009 * params.alpha,
  );
  let ownCell = gridCell(position);

  for (var offsetY = -4; offsetY <= 4; offsetY = offsetY + 1) {
    for (var offsetX = -4; offsetX <= 4; offsetX = offsetX + 1) {
      let candidateX = i32(ownCell.x) + offsetX;
      let candidateY = i32(ownCell.y) + offsetY;
      if (
        candidateX < 0 || candidateY < 0
        || candidateX >= i32(params.gridWidth)
        || candidateY >= i32(params.gridHeight)
      ) {
        continue;
      }
      let cellIndex = u32(candidateY) * params.gridWidth + u32(candidateX);
      let count = min(atomicLoad(&gridCounts[cellIndex]), params.maxPerCell);
      for (var slot = 0u; slot < count; slot = slot + 1u) {
        let otherIndex = gridIndices[cellIndex * params.maxPerCell + slot];
        if (otherIndex == index || otherIndex >= params.nodeCount) {
          continue;
        }
        var delta = position - currentPositions[otherIndex];
        if (abs(delta.x) + abs(delta.y) < 0.001) {
          let direction = select(-1.0, 1.0, index > otherIndex);
          delta = vec2<f32>(
            direction * (0.01 + f32((index + otherIndex) % 7u) * 0.002),
            direction * (0.008 + f32((index * 3u + otherIndex) % 5u) * 0.002),
          );
        }
        let rawDistance = max(0.001, length(delta));
        let distanceSquared = max(36.0, dot(delta, delta));
        let distance = sqrt(distanceSquared);
        let magnitude = (
          params.repulsionStrength * 40.0 * masses[otherIndex] * params.alpha
          / distanceSquared
        );
        force = force + delta / distance * magnitude;
        let minimumDistance = collisionRadii[index] + collisionRadii[otherIndex];
        if (rawDistance < minimumDistance) {
          let collisionMagnitude = (minimumDistance - rawDistance) * 0.38;
          force = force + delta / rawDistance * collisionMagnitude;
        }
      }
    }
  }

  let adjacencyStart = adjacencyOffsets[index];
  let adjacencyEnd = adjacencyOffsets[index + 1u];
  for (var cursor = adjacencyStart; cursor < adjacencyEnd; cursor = cursor + 1u) {
    let targetIndex = adjacencyTargets[cursor];
    if (targetIndex >= params.nodeCount) {
      continue;
    }
    let delta = currentPositions[targetIndex] - position;
    let distance = max(1.0, length(delta));
    let weight = max(0.08, adjacencyWeights[cursor]);
    let magnitude = (
      (distance - params.linkDistance) * 0.004 * params.linkStrength
      * weight * params.alpha
    );
    force = force + delta / distance * magnitude;
  }

  var velocity = (
    currentVelocities[index] + force / max(0.5, masses[index])
  ) * params.damping;
  let speed = length(velocity);
  if (speed > 10.0) {
    velocity = velocity * (10.0 / speed);
  }
  nextPositions[index] = position + velocity;
  nextVelocities[index] = velocity;
}
