import type { Component } from 'vue'
import GenericArtifactContent from './GenericArtifactContent.vue'
import MediaArtifactContent from './MediaArtifactContent.vue'

const renderers = new Map<string, Component>()

export function registerArtifactRenderer(schemaName: string, component: Component): void {
  renderers.set(schemaName, component)
}

export function resolveArtifactRenderer(schemaName: string, type?: string): Component {
  return renderers.get(schemaName) ?? (type === 'image' || type === 'video' ? MediaArtifactContent : GenericArtifactContent)
}
