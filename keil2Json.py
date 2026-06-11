import os
import json
import argparse
import xml.etree.ElementTree as ET
from pathlib import Path
import shlex


def resolve_with_real_case(project_root, relative_path):
    """
    解析路径并纠正大小写以匹配真实文件系统。

    在 Windows（大小写不敏感）上，Path.resolve() 会自动纠正大小写。
    但在 Linux（大小写敏感）上，Path.resolve() 严格保留输入的大小写，
    导致 source/EVSE 无法匹配真实目录 source/evse。

    本函数逐级遍历路径的每个组件，如果组件在文件系统中不存在，
    则在父目录中做大小写不敏感的匹配，找到真实的目录/文件名。
    """
    # 先合并 project_root 和相对路径，然后分解为组件列表
    raw_path = project_root / relative_path
    parts = raw_path.parts  # 例如 ('/', 'root', 'code', 'main_board', 'source', 'EVSE')

    current = Path(parts[0])  # 根目录，如 '/' 或 'C:\\'

    for part in parts[1:]:
        if part == '..':
            current = current.parent
        elif part == '.':
            continue
        else:
            target = current / part
            if target.exists():
                current = target
            else:
                # 大小写不敏感匹配：在父目录中找同名但大小写不同的条目
                found = False
                try:
                    for entry in current.iterdir():
                        if entry.name.lower() == part.lower():
                            current = entry
                            found = True
                            break
                except (PermissionError, OSError):
                    pass
                if not found:
                    current = target  # 确实不存在，保留原名

    return str(current).replace('\\', '/')


class CompileCommandsGenerator:
    def __init__(self, path=None, absolute=False):
        self.path = path if path else '.'
        self.absolute = absolute
        self.project_root = None
        self.include_paths = []
        self.defines = []
        self.source_files = []

    def parse_uvprojx(self, file_path, project_root):
        # 解析XML文件
        tree = ET.parse(file_path)
        root = tree.getroot()

        # 精确查找 IncludePath 和 Define
        various_controls = root.find('.//TargetArmAds/Cads/VariousControls')
        include_paths = []
        defines = []

        if various_controls is not None:
            # 提取 IncludePath
            include_elem = various_controls.find('IncludePath')
            if include_elem is not None and include_elem.text:
                include_paths = include_elem.text.split(';')

            # 提取 Define
            define_elem = various_controls.find('Define')
            if define_elem is not None and define_elem.text:
                defines = define_elem.text.split(',')

        # 转换IncludePath为绝对路径
        absolute_include_paths = []
        for path in include_paths:
            clean_path = path.strip().replace('\\', '/')
            if not clean_path:
                continue
            # 构建绝对路径
            abs_path = resolve_with_real_case(project_root, clean_path)
            absolute_include_paths.append(abs_path)

        # 处理Define中的空格
        defines = [d.strip() for d in defines if d.strip()]

        # 获取所有源文件路径并转换绝对路径
        source_files = []
        for group in root.findall('.//Group'):
            for file_elem in group.findall('.//File'):
                file_path_elem = file_elem.find('FilePath')
                if file_path_elem is not None and file_path_elem.text:
                    file_path = file_path_elem.text.strip().replace('\\', '/')
                    # 构建绝对路径
                    abs_file_path = resolve_with_real_case(project_root, file_path)
                    source_files.append(abs_file_path)

        return absolute_include_paths, defines, source_files

    def generate_entries(self, include_paths, defines, source_files):
        # 获取 compile_commands.json 所在目录的绝对路径（用于相对路径计算）
        compile_dir = self.project_root #Path.cwd().resolve()
        compile_dir_str = str(compile_dir).replace("\\", "/")  # 统一路径分隔符 [[6]]

        # 处理 Include 路径：根据 self.absolute 决定是否转为相对路径
        processed_include_paths = []
        for path in include_paths:
            abs_path = Path(path).resolve()
            if not self.absolute:
                try:
                    # 使用 os.path.relpath 替代 relative_to，支持跨子目录相对路径 [[10]]
                    rel_path = os.path.relpath(str(abs_path), str(compile_dir))
                    # 替换路径分隔符为 '/'，确保兼容性 [[4]]
                    processed_include_paths.append(rel_path.replace("\\", "/"))
                except ValueError:
                    # 如果路径跨驱动器（如 C:\ vs D:\），保留绝对路径并替换分隔符 [[10]]
                    processed_include_paths.append(str(abs_path).replace("\\", "/"))
            else:
                # 保留绝对路径并替换分隔符 [[6]]
                processed_include_paths.append(str(abs_path).replace("\\", "/"))

        # 构建基础编译参数
        base_args = [
            # "-c",
            "-D__GNUC__",
        ] + [f"-I{p}" for p in processed_include_paths] + \
        [f"-D{define}" for define in defines]
        
        compiler = "arm-none-eabi-gcc"
        # 处理源文件路径：根据 self.absolute 决定是否转为相对路径
        entries = []
        for file in source_files:
            file_path = Path(file).resolve()
            if not self.absolute:
                try:
                    # 使用 os.path.relpath 支持跨子目录相对路径 [[10]]
                    rel_file = os.path.relpath(str(file_path), str(compile_dir))
                    # 替换路径分隔符为 '/' [[4]]
                    file_entry = rel_file.replace("\\", "/")
                except ValueError:
                    # 如果路径跨驱动器，保留绝对路径 [[10]]
                    file_entry = str(file_path).replace("\\", "/")
            else:
                # 保留绝对路径并替换分隔符 [[6]]
                file_entry = str(file_path).replace("\\", "/")

            # command_args = base_args + [file_entry]
            # command_str = compiler + " " + "-c " + file_entry + " " + "-IC:/Keil_v5/Packs/ARM/CMSIS/5.9.0/CMSIS/Core/Include " + " ".join(shlex.quote(arg) for arg in base_args)
            command_str = compiler + " " + "-c " + file_entry + " " +  " ".join(shlex.quote(arg) for arg in base_args)

            # 构建 JSON 条目
            entry = {
                "command": command_str,
                "arguments": base_args.copy(),
                "directory": compile_dir_str,  # 始终为绝对路径且分隔符统一 [[6]]
                "file": file_entry
            }
            entries.append(entry)

        return entries

    def write_json(self, entries):
        with open('compile_commands.json', 'w', encoding='utf-8') as f:
            json.dump(entries, f, indent=4, ensure_ascii=False)

    def generate(self):
        # 查找当前目录下的uvprojx文件
        uvprojx_files = list(Path(self.path).glob('**/*.uvprojx'))
        if not uvprojx_files:
            print("cannot find any .uvprojx file in current directory")
            return

        # 处理第一个找到的uvprojx文件
        uvprojx_path = uvprojx_files[0]
        self.project_root = uvprojx_path.parent.resolve()
        self.include_paths, self.defines, self.source_files = self.parse_uvprojx(uvprojx_path, self.project_root)

        entries = self.generate_entries(self.include_paths, self.defines, self.source_files)
        self.write_json(entries)
        print(f"generate complete: compile_commands.json ({'absolute path' if self.absolute else 'relative path'})")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Generate compile_commands.json for vscode')
    parser.add_argument('--path', '-p', required=False, help='Specify the path of .uvprojx file')
    parser.add_argument('--absolute', '-a', action='store_true', required=False, help='Format with Absolute path')
    args = parser.parse_args()

    generator = CompileCommandsGenerator(path=args.path, absolute=args.absolute)
    generator.generate()
