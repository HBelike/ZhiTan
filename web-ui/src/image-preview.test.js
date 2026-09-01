import assert from 'node:assert/strict'
import test from 'node:test'

import { decorateOpenableImages, resolveImagePreviewRequest } from './image-preview.js'

function createFakeElement(tagName) {
  const attributes = new Map()
  const classes = new Set()
  return {
    tagName,
    attributes,
    classes,
    child: null,
    setAttribute(name, value) {
      attributes.set(name, value)
    },
    removeAttribute(name) {
      attributes.delete(name)
    },
    getAttribute(name) {
      return attributes.get(name) ?? null
    },
    classList: {
      add(name) {
        classes.add(name)
      }
    },
    append(child) {
      this.child = child
    }
  }
}

test('decorateOpenableImages 用原图链接包裹正文图片', () => {
  const link = createFakeElement('A')
  const image = createFakeElement('IMG')
  image.currentSrc = '/api/media-assets/131/file'
  image.src = 'http://localhost/api/media-assets/131/file'
  image.closest = () => null
  image.ownerDocument = {
    createElement(tagName) {
      assert.equal(tagName, 'a')
      return link
    }
  }
  image.replaceWith = (replacement) => {
    assert.equal(replacement, link)
  }
  const container = {
    querySelectorAll(selector) {
      assert.equal(selector, 'img')
      return [image]
    }
  }

  decorateOpenableImages(container)

  assert.equal(link.child, image)
  assert.equal(link.attributes.get('href'), '/api/media-assets/131/file')
  assert.equal(link.attributes.has('target'), false)
  assert.equal(link.attributes.has('rel'), false)
  assert.equal(link.attributes.get('data-image-preview-url'), '/api/media-assets/131/file')
  assert.equal(link.attributes.get('aria-label'), '预览图片')
  assert.equal(link.attributes.get('title'), '点击预览图片')
  assert.equal(link.classes.has('article-image-link'), true)
})

test('decorateOpenableImages 复用正文图片已有链接并指向原图', () => {
  const link = createFakeElement('A')
  const image = createFakeElement('IMG')
  image.src = '/images/project.png'
  image.closest = (selector) => {
    assert.equal(selector, 'a')
    return link
  }
  const container = {
    querySelectorAll() {
      return [image]
    }
  }

  decorateOpenableImages(container)

  assert.equal(link.child, null)
  assert.equal(link.attributes.get('href'), '/images/project.png')
  assert.equal(link.attributes.has('target'), false)
  assert.equal(link.attributes.has('rel'), false)
})

test('resolveImagePreviewRequest 从正文图片链接生成应用内预览请求', () => {
  const link = createFakeElement('A')
  const image = createFakeElement('IMG')
  link.setAttribute('data-image-preview-url', '/api/media-assets/131/file')
  image.setAttribute('alt', '工程技术教学图')
  link.querySelector = (selector) => {
    assert.equal(selector, 'img')
    return image
  }
  link.closest = (selector) => {
    assert.equal(selector, 'a.article-image-link')
    return link
  }

  assert.deepEqual(resolveImagePreviewRequest(link), {
    source: '/api/media-assets/131/file',
    alt: '工程技术教学图'
  })
})

test('resolveImagePreviewRequest 忽略正文图片以外的点击', () => {
  const target = {
    closest(selector) {
      assert.equal(selector, 'a.article-image-link')
      return null
    }
  }

  assert.equal(resolveImagePreviewRequest(target), null)
})

test('decorateOpenableImages 忽略没有地址的图片', () => {
  const image = createFakeElement('IMG')
  image.src = ''
  image.currentSrc = ''
  image.closest = () => null
  let created = false
  image.ownerDocument = {
    createElement() {
      created = true
    }
  }
  const container = {
    querySelectorAll() {
      return [image]
    }
  }

  decorateOpenableImages(container)

  assert.equal(created, false)
})
