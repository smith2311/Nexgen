from django import template

register = template.Library()


@register.filter
def split(value, sep=','):
    return str(value).split(sep)
